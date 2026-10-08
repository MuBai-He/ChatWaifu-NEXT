import PptxGenJS from "pptxgenjs";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const presentation = new PptxGenJS();
presentation.layout = "LAYOUT_WIDE";
presentation.title = input.title;
presentation.author = "ChatWaifu";
presentation.subject = input.title;
const font = input._font_face ?? "Noto Sans CJK SC";
presentation.theme = { headFontFace: font, bodyFontFace: font, lang: "zh-CN" };
const cover = presentation.addSlide();
cover.background = { color: "F5F7FB" };
cover.addText(input.title, {
  x: 0.7,
  y: 2.2,
  w: 11.9,
  h: 2,
  fontSize: 28,
  color: "23314F",
  breakLine: false,
  valign: "mid",
  margin: 0,
});
for (const item of input.slides) {
  const pages = [];
  let current = [],
    lines = 0;
  // Each segment fits one page even when one original bullet is very long.
  const segments = item.bullets.flatMap((text) => {
    const characters = Array.from(text),
      parts = [];
    for (let offset = 0; offset < characters.length; offset += 320)
      parts.push(characters.slice(offset, offset + 320).join(""));
    return parts;
  });
  for (const text of segments) {
    const needed = Math.ceil(Array.from(text).length / 40) + 1;
    if (lines + needed > 11 && current.length) {
      pages.push(current);
      current = [];
      lines = 0;
    }
    current.push(text);
    lines += needed;
  }
  if (current.length) pages.push(current);
  for (const [index, bullets] of pages.entries()) {
    const slide = presentation.addSlide();
    slide.background = { color: "FFFFFF" };
    slide.addText(item.title + (index ? "（续）" : ""), {
      x: 0.65,
      y: 0.3,
      w: 12,
      h: 1.4,
      fontSize: item.title.length > 60 ? 18 : 24,
      bold: true,
      color: "23314F",
      margin: 0,
    });
    slide.addText(
      bullets.map((text) => ({
        text,
        options: { bullet: { indent: 18 }, breakLine: true },
      })),
      {
        x: 0.8,
        y: 1.9,
        w: 11.7,
        h: 4.8,
        fontSize: 20,
        color: "323B4D",
        paraSpaceAfterPt: 12,
        valign: "top",
        margin: 0,
      },
    );
  }
}
await presentation.writeFile({ fileName: process.argv[2] });
