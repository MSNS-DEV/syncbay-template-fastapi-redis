// A small, safe Markdown renderer: HTML is escaped first, then a subset of Markdown is applied.
// Supported: headings, paragraphs, bold, italic, inline code, fenced code, lists, links, quotes.
(function (global) {
  "use strict";

  function escapeHtml(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeHref(url) {
    var u = url.trim();
    if (/^(https?:|mailto:)/i.test(u) || /^\/(?!\/)/.test(u) || u.charAt(0) === "#") return u;
    return null;
  }

  function inline(text) {
    var codes = [];
    var out = escapeHtml(text).replace(/`([^`]+)`/g, function (_, code) {
      codes.push("<code>" + code + "</code>");
      return "\u0000" + (codes.length - 1) + "\u0000";
    });
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (match, label, href) {
      var safe = safeHref(href.replace(/&amp;/g, "&"));
      if (!safe) return label;
      var external = /^https?:/i.test(safe);
      return '<a href="' + escapeHtml(safe) + '"' +
        (external ? ' rel="noopener noreferrer nofollow" target="_blank"' : "") + ">" + label + "</a>";
    });
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    out = out.replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, "$1<em>$2</em>");
    return out.replace(/\u0000(\d+)\u0000/g, function (_, i) { return codes[Number(i)]; });
  }

  function render(markdown) {
    var lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    var html = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (/^```/.test(line)) {
        var code = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
        i++;
        html.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
        continue;
      }
      var heading = /^(#{1,4})\s+(.*)$/.exec(line);
      if (heading) {
        // Note headings start at h3: the page already uses h1 and h2.
        var level = Math.min(heading[1].length + 2, 6);
        html.push("<h" + level + ">" + inline(heading[2]) + "</h" + level + ">");
        i++;
        continue;
      }
      if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
        var ordered = /^\s*\d+[.)]\s+/.test(line);
        var items = [];
        while (i < lines.length && (ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/).test(lines[i])) {
          items.push("<li>" + inline(lines[i].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "")) + "</li>");
          i++;
        }
        var tag = ordered ? "ol" : "ul";
        html.push("<" + tag + ">" + items.join("") + "</" + tag + ">");
        continue;
      }
      if (/^>\s?/.test(line)) {
        var quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^>\s?/, ""));
        html.push("<blockquote><p>" + inline(quote.join(" ")) + "</p></blockquote>");
        continue;
      }
      if (!line.trim()) {
        i++;
        continue;
      }
      var para = [lines[i++]];
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|>|\s*[-*+]\s|\s*\d+[.)]\s)/.test(lines[i])) {
        para.push(lines[i++]);
      }
      html.push("<p>" + inline(para.join(" ")) + "</p>");
    }
    return html.join("\n");
  }

  global.Markdown = { render: render, escapeHtml: escapeHtml };
})(window);
