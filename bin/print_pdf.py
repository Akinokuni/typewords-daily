#!/usr/bin/env python3
"""推送打印：OSS 上传 + MQTT 下发 + 回执监听 + OSS 落地核对。

用法: print_pdf.py <pdf> [--copies 1] [--ack-timeout 45]
先订阅 home/printer/# 再下发（status 回执不 retained，晚一步就错过）。
输出 JSON: {success, job_id, object_key, size, md5, etag, oss_verified, ack, clients_connected}
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
import uuid

AGENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(AGENT, "bin"))
import twlib as T  # noqa: E402


def load_cfg(rel):
    import yaml
    with open(os.path.join(AGENT, rel), encoding="utf-8") as f:
        return yaml.safe_load(f)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def start_listener(cfg, msgs):
    import paho.mqtt.client as mqtt
    mq, mqq = cfg["mqtt"], cfg["topics"]
    state = {"connected": False, "clients_connected": None}

    def on_connect(client, userdata, flags, rc, properties=None):
        state["connected"] = True
        client.subscribe("home/printer/#", qos=1)
        client.subscribe("$SYS/broker/clients/connected", qos=0)

    def on_message(client, userdata, m):
        payload = m.payload.decode("utf-8", "replace")
        msgs.append({"topic": m.topic, "payload": payload, "ts": time.time()})
        if m.topic == "$SYS/broker/clients/connected":
            state["clients_connected"] = payload.strip()

    client = mqtt.Client(
        client_id="tw-daily-agent-ack",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    if mqq.get("username"):
        client.username_pw_set(mqq["username"], mqq.get("password"))
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(mq["broker_host"], mq["broker_port"], keepalive=30)
    client.loop_start()
    return client, state


def head_object(cfg, key):
    import oss2
    o, s = cfg["oss"], cfg["oss"]
    auth = oss2.Auth(o["access_key_id"], o["access_key_secret"])
    bucket = oss2.Bucket(auth, s["endpoint"], s["bucket_name"])
    h = bucket.head_object(key)
    return {"size": h.content_length, "etag": (h.etag or "").strip('"')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--copies", type=int, default=1)
    ap.add_argument("--ack-timeout", type=int, default=45)
    ap.add_argument("--retries", type=int, default=2)
    a = ap.parse_args()

    wf = T.load_workflow()
    cfg = load_cfg(wf.get("print_config", "config/print.yaml"))
    sys.path.insert(0, wf.get("print_skill_scripts",
                              "/root/.hermes/skills/productivity/print/scripts"))
    from oss_uploader import upload_and_sign        # noqa: E402
    from mqtt_publisher import publish_print_job    # noqa: E402

    pdf = os.path.abspath(a.pdf)
    if not os.path.isfile(pdf):
        print(json.dumps({"success": False, "message": f"文件不存在 {pdf}"}, ensure_ascii=False))
        return 1

    oss_cfg, mqtt_cfg, topics = cfg["oss"], cfg["mqtt"], cfg["topics"]
    job_id = str(uuid.uuid4())[:8]
    key = f"{oss_cfg.get('prefix', 'prints/')}job_{job_id}.pdf"
    local_md5 = md5(pdf)

    msgs = []
    client, state = start_listener(cfg, msgs)
    t0 = time.time()
    while not state["connected"] and time.time() - t0 < 10:
        time.sleep(0.2)
    time.sleep(1.0)  # 让订阅在客户端侧生效后再下发

    result = {"success": False, "job_id": job_id, "object_key": key,
              "size": os.path.getsize(pdf), "md5": local_md5, "etag": None,
              "oss_verified": False, "ack": None, "clients_connected": None, "message": ""}

    try:
        url = upload_and_sign(
            local_path=pdf, object_key=key,
            access_key_id=oss_cfg["access_key_id"],
            access_key_secret=oss_cfg["access_key_secret"],
            endpoint=oss_cfg["endpoint"], bucket_name=oss_cfg["bucket_name"],
            expires=oss_cfg.get("presign_expires", 300),
        )
    except Exception as e:
        result["message"] = f"OSS 上传失败: {e!r}"
        _cleanup(client)
        print(json.dumps(result, ensure_ascii=False))
        return 1

    try:
        ok = publish_print_job(
            job_id=job_id, presigned_url=url, copies=a.copies,
            broker_host=mqtt_cfg["broker_host"], broker_port=mqtt_cfg["broker_port"],
            topic=topics["task"], qos=mqtt_cfg.get("qos", 1),
            client_id=mqtt_cfg.get("client_id", "ai-agent-publisher"),
            username=mqtt_cfg.get("username"), password=mqtt_cfg.get("password"),
            timeout=mqtt_cfg.get("publish_timeout", 10),
        )
    except Exception as e:
        result["message"] = f"MQTT 下发异常: {e!r}"
        _cleanup(client)
        print(json.dumps(result, ensure_ascii=False))
        return 1

    result["success"] = bool(ok)
    result["message"] = "已下发，等待本地端执行" if ok else "MQTT 发布失败"
    result["publish_attempts"] = 1

    # OSS 落地核对（上传件大小 + ETag 应等于本地 MD5）
    try:
        h = head_object(cfg, key)
        result["etag"] = h["etag"]
        result["oss_verified"] = (h["size"] == result["size"]
                                  and h["etag"].lower() == local_md5.lower())
    except Exception as e:
        result["message"] += f" | OSS 复核失败: {e!r}"

    def printer_ack():
        """真实回执：topic 不是本机下发通道（那是回声）、payload 里带 job_id。

        取**最新**一条：status 通道非 retained，且 downloading→printing→success 常连发。
        """
        for m in reversed(msgs):
            if m["topic"] in ("$SYS/broker/clients/connected", topics["task"]):
                continue
            if m["ts"] >= t0 and job_id in m["payload"]:
                return m
        return None

    def _status_of(payload):
        try:
            return str(json.loads(payload).get("status") or "")
        except Exception:
            return ""

    def republish():
        return publish_print_job(
            job_id=job_id, presigned_url=url, copies=a.copies,
            broker_host=mqtt_cfg["broker_host"], broker_port=mqtt_cfg["broker_port"],
            topic=topics["task"], qos=mqtt_cfg.get("qos", 1),
            client_id=mqtt_cfg.get("client_id", "ai-agent-publisher"),
            username=mqtt_cfg.get("username"), password=mqtt_cfg.get("password"),
            timeout=mqtt_cfg.get("publish_timeout", 10),
        )

    TERMINAL = ("success", "error", "failed")
    GRACE_AFTER_FIRST = 6.0  # 首次回执后再收一会儿，等 downloading→printing→success 连发
    ack, chain = None, []
    for attempt in range(1, max(1, int(a.retries)) + 1):
        deadline = time.time() + a.ack_timeout
        grace_end = None
        while time.time() < deadline:
            m = printer_ack()
            if m and (ack is None or m["payload"] != ack.get("payload")):
                st = _status_of(m["payload"])
                ack = {"topic": m["topic"], "payload": m["payload"],
                       "latency_s": round(m["ts"] - t0, 1), "source": "printer",
                       "status": st}
                if st and (not chain or chain[-1]["status"] != st):
                    chain.append({"status": st, "ts": round(m["ts"] - t0, 1)})
                if grace_end is None:
                    grace_end = time.time() + GRACE_AFTER_FIRST
            if ack is not None and (ack["status"] in TERMINAL
                                    or (grace_end and time.time() >= grace_end)):
                break
            time.sleep(0.3)
        if ack:
            break
        if attempt < max(1, int(a.retries)):
            result["publish_attempts"] = attempt + 1
            try:
                republish()
                result["message"] = f"未收回执，已重发（第 {attempt + 1} 次）"
            except Exception as e:
                result["message"] += f" | 重发异常: {e!r}"
    if ack:
        ack["ack_chain"] = chain
    result["ack"] = ack

    state_msgs = [m for m in msgs
                  if m["topic"].endswith("/status") and m["topic"] != topics["task"]
                  and m["ts"] >= t0]
    if not result["ack"] and state_msgs:
        m = state_msgs[-1]
        result["ack"] = {"topic": m["topic"], "payload": m["payload"],
                         "latency_s": round(m["ts"] - t0, 1), "source": "status-after-publish"}

    result["clients_connected"] = state.get("clients_connected")
    if not result["ack"]:
        result["message"] += " | 未捕获打印机回执（本地打印端可能离线）"
    elif result["ack"].get("status") in ("success", "printing"):
        result["message"] = (f"已下发并经打印机回执确认：{result['ack']['status']}"
                             f"（{result['ack'].get('latency_s')}s，job={result['job_id']}）")

    _cleanup(client)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


def _cleanup(client):
    try:
        client.loop_stop()
        client.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())