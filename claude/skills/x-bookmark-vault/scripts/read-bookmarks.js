// X Bookmark Vault: read the rendered X Bookmarks timeline in Claude in Chrome.
// Run the whole file with javascript_tool in a signed-in tab on the Bookmarks
// timeline (https://x.com/i/bookmarks). It reads only text that the page has
// rendered and scrolls the page to load more posts. It does not read cookies
// or storage, call X APIs, or make network requests.
// Each row gives `handle` and `id` rather than a URL, because the extension
// redacts long link paths in results. Build the post URL as
// https://x.com/<handle>/status/<id> and use the handle as the author.
// Change LIMIT for a smaller selection. For a continuation, set AFTER_ID
// to the string returned by vault cursor.
await (async () => {
  const LIMIT = 20;
  const AFTER_ID = null;
  const FROM_CURRENT_POSITION = false;
  const STEP_WAIT_MS = 1200;
  const MAX_IDLE_STEPS = 4;
  const MAX_STEPS = 60;

  const tab = document.querySelector('main [role="tab"][aria-selected="true"]');
  const onBookmarks = /^\/i\/(bookmarks|history)\/?$/.test(location.pathname) &&
    (!tab || /bookmark/i.test(tab.textContent));
  if (!onBookmarks) {
    return { error: "Open the Bookmarks timeline at https://x.com/i/bookmarks first.", page: location.pathname };
  }

  // Text as rendered, keeping emoji images and the full text of shortened links.
  const textOf = (root) => {
    let out = "";
    const walk = (node) => {
      if (node.nodeType === Node.TEXT_NODE) { out += node.nodeValue; return; }
      if (node.nodeType !== Node.ELEMENT_NODE || node.getAttribute("aria-hidden") === "true") return;
      if (node.tagName === "IMG") { out += node.getAttribute("alt") || ""; return; }
      if (node.tagName === "BR") { out += "\n"; return; }
      node.childNodes.forEach(walk);
    };
    walk(root);
    return out.replace(/ /g, " ").trim();
  };

  const rows = [];
  const seen = new Set();
  let foundAnchor = AFTER_ID === null;
  const target = LIMIT + (AFTER_ID === null ? 0 : 1);
  const collect = () => {
    for (const article of document.querySelectorAll('main article[data-testid="tweet"]')) {
      // A quoted post is a nested card with its own avatar; X Article cards have none.
      const quote = [...article.querySelectorAll('div[role="link"]')]
        .find((card) => card.querySelector('[data-testid^="UserAvatar-Container"]'));
      const own = (el) => !quote || !quote.contains(el);
      const permalink = [...article.querySelectorAll('a[href*="/status/"]')]
        .find((a) => a.querySelector("time") && own(a));
      if (!permalink) continue;
      const [, handle, kind, id] = new URL(permalink.href).pathname.split("/");
      if (kind !== "status" || !/^\d{1,19}$/.test(id) || seen.has(id)) continue;
      seen.add(id);
      if (id === AFTER_ID) {
        foundAnchor = true;
        rows.push({handle, id});
        continue;
      }
      if (!foundAnchor) continue;

      const textNode = [...article.querySelectorAll('[data-testid="tweetText"]')].find(own);
      const cover = article.querySelector('[data-testid="article-cover-image"]');
      let text = textNode ? textOf(textNode) : "";
      let partial = [...article.querySelectorAll('[data-testid="tweet-text-show-more-link"]')].some(own);
      if (!textNode && cover) {
        let card = cover;
        while (card.parentElement && card !== article && card.innerText.trim().length < 20) card = card.parentElement;
        text = card.innerText.replace(/^\s*Article\s*\n/, "").trim();
        partial = true;
      }

      if (quote) {
        const quoteText = quote.querySelector('[data-testid="tweetText"]');
        const quoteHandle = [...quote.querySelectorAll("span")].map((s) => s.textContent)
          .find((s) => /^@[A-Za-z0-9_]{1,15}$/.test(s));
        if (quoteText) text += `\n\n[Quoted post${quoteHandle ? " " + quoteHandle : ""}]\n${textOf(quoteText)}`;
      }

      const name = article.querySelector('[data-testid="User-Name"]');
      rows.push({
        handle, id,
        created_at: permalink.querySelector("time").getAttribute("datetime"),
        text,
        partial,
        unavailable_videos: article.querySelectorAll('[data-testid="videoPlayer"]').length,
        protected: !!(name && name.querySelector('[data-testid="icon-lock"], svg[aria-label="Protected account"]')),
      });
    }
  };

  if (!FROM_CURRENT_POSITION) {
    window.scrollTo(0, 0);
    await new Promise((r) => setTimeout(r, STEP_WAIT_MS));
  }
  let idle = 0;
  for (let step = 0; step < MAX_STEPS && rows.length < target && idle < MAX_IDLE_STEPS; step++) {
    const before = seen.size;
    const offset = window.scrollY;
    collect();
    if (rows.length >= target) break;
    window.scrollBy(0, Math.round(window.innerHeight * 0.8));
    await new Promise((r) => setTimeout(r, STEP_WAIT_MS));
    idle = seen.size === before && window.scrollY === offset ? idle + 1 : 0;
  }
  collect();
  if (!foundAnchor) {
    if (idle < MAX_IDLE_STEPS) {
      return {continue_scan: true};
    }
    return {error: "The previous bookmark could not be found on this timeline. Restart from recent bookmarks or try again after opening the same account."};
  }
  window.scrollTo(0, 0);
  const selected = rows.slice(0, target);
  return {
    read_at: new Date().toISOString(),
    count: selected.length - (AFTER_ID === null ? 0 : 1),
    rows: selected,
  };
})()
