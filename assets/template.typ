// marginRuby-reader：面向纸质阅读与手写批注的英文阅读模板。
// Typst 0.15+

// 英文主文本使用 Libertinus Serif（Charter 风格的强壮衬线体），中文及
// Ruby 使用 Source Han Sans SC。两套字体随 skill 内置；英文文字优先使用
// Libertinus，遇到中文字符时回退到思源黑体。
#let english-font = ("Libertinus Serif", "Source Han Sans SC")
#let ruby-font = "Source Han Sans SC"

/// 在英文单词上方排版中文旁注（Ruby）。
///
/// `base` 是英文正文，`rt` 是上方的中文注音或释义。旁注使用思源黑体
/// 5.5pt、luma(120)，正文使用 9.5pt 强壮衬线体、纯黑。`weight` 可传入
/// `"bold"` 来标记生词。
#let ruby(base, rt, weight: "regular") = {
  // 不手工设置 box 的百分比基线。默认 auto 会继承网格末行英文文本的
  // 实际字体基线，使带 Ruby 的单词与同一行普通英文精确对齐。
  box(
    grid(
      columns: 1,
      align: center + bottom,
      row-gutter: 0.15em,
      text(font: ruby-font, size: 5.5pt, fill: luma(120), weight: "regular")[#rt],
      text(font: english-font, size: 9.5pt, fill: luma(0), weight: weight)[#base],
    ),
  )
}

/// 生词快捷宏：可单独加粗英文，也可同时提供中文旁注。
#let vocabulary(base, rt: none) = {
  if rt == none {
    text(font: english-font, size: 9.5pt, fill: luma(0), weight: "bold")[#base]
  } else {
    ruby(base, rt, weight: "bold")
  }
}

/// 正文下方的单词精讲条目。
///
/// `word`、`pronunciation`、`part` 构成单词标题；`meaning` 是中文释义。
/// `example` 与 `translation` 可选，用于展示原文语境和对应译文。
#let word-detail(
  word,
  pronunciation: none,
  part: none,
  meaning: none,
  example: none,
  translation: none,
) = {
  block(width: 100%, breakable: false)[
    #text(font: english-font, size: 9.5pt, weight: "bold")[#word]
    #if pronunciation != none {
      h(0.55em)
      text(font: english-font, size: 8pt, fill: luma(42%))[#pronunciation]
    }
    #if part != none {
      h(0.55em)
      text(font: english-font, size: 7.5pt, style: "italic", fill: luma(42%))[#part]
    }
    #if meaning != none {
      h(0.75em)
      text(font: ruby-font, size: 8.5pt, fill: luma(10%))[#meaning]
    }
    #if example != none {
      linebreak()
      text(font: english-font, size: 8.5pt, style: "italic", fill: luma(15%))[#example]
    }
    #if translation != none {
      h(0.75em)
      text(font: ruby-font, size: 8pt, fill: luma(42%))[(#translation)]
    }
  ]
}

/// 将若干 `word-detail` 条目组合为正文后的单词示意模块。
#let word-focus(title: "Words in context", note: "原文重点词", body) = {
  heading(level: 2)[
    #title
    #if note != none {
      h(0.75em)
      text(font: ruby-font, size: 7pt, weight: "regular", fill: luma(48%))[#note]
    }
  ]
  body
}

/// marginRuby-reader 模板。
///
/// 版面固定为 A4，左 2cm、右 6cm、上/下 2.5cm；右侧宽阔空白区用于
/// 手写互动。正文采用 9.5pt 衬线体、1.6em 行距和两端对齐。
#let margin-ruby-reader(
  title: "Margin Ruby Reader",
  author: none,
  date: none,
  header-note: none,
  numbering: "1",
  body,
) = {
  set document(
    title: title,
    author: if author == none { () } else { (author,) },
  )

  set page(
    paper: "a4",
    margin: (left: 2cm, right: 6cm, top: 2.5cm, bottom: 2.5cm),
    numbering: numbering,
    header: context {
      grid(
        columns: (1fr, auto),
        align: horizon,
        gutter: 1em,
        [#text(font: english-font, size: 8.5pt, weight: "bold", fill: luma(0))[#title]],
        [#text(size: 7.5pt, fill: luma(65%))[#if author != none {author}#if date != none [ · #date]#if header-note != none [ · #header-note]]],
      )
    },
    footer: context {
      align(right)[
        #text(size: 7.5pt, fill: luma(65%))[#counter(page).display()]
      ]
    },
  )

  set text(font: english-font, size: 9.5pt, fill: luma(0), lang: "en")
  set par(leading: 1.6em, justify: true)

  // 让标题保持简洁，不改变正文的行距设定。
  show heading.where(level: 1): it => {
    v(0.7em)
    text(font: english-font, size: 12pt, weight: "bold", fill: luma(0))[#it.body]
    v(0.2em)
  }
  show heading.where(level: 2): it => {
    v(0.45em)
    text(font: english-font, size: 10.5pt, weight: "bold", fill: luma(0))[#it.body]
    v(0.12em)
  }

  body
}
