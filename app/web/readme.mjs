// A plugin's README.md in the store (version 24): # and ## headings, paragraphs, - and 1. lists, **bold** and `code`.
// Everything else stays text; HTML in it is shown as written, never run.
const escape = (text) => text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const inline = (text) => escape(text).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

export function readmeHtml(markdown) {
  const html = [];
  let paragraph = [], list = null;
  const endParagraph = () => {
    if (paragraph.length) html.push(`<p>${inline(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const endList = () => {
    if (list) html.push(`</${list}>`);
    list = null;
  };
  for (const line of String(markdown).split(/\r?\n/)) {
    const heading = /^(#{1,3})\s+(.*)$/.exec(line), item = /^(-|\d+\.)\s+(.*)$/.exec(line.trim());
    if (heading) {
      endParagraph(); endList();
      const level = heading[1].length + 1;  // the page's dialog title is h2
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
    } else if (item) {
      endParagraph();
      const kind = item[1] === "-" ? "ul" : "ol";
      if (list !== kind) { endList(); html.push(`<${kind}>`); list = kind; }
      html.push(`<li>${inline(item[2])}</li>`);
    } else if (!line.trim()) {
      endParagraph(); endList();
    } else {
      endList();
      paragraph.push(line.trim());
    }
  }
  endParagraph(); endList();
  return html.join("");
}
