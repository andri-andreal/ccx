<script>
  import { marked } from "marked";
  import DOMPurify from "dompurify";
  let { text = "" } = $props();
  const html = $derived(DOMPurify.sanitize(marked.parse(String(text), { async:false, gfm:true, breaks:true }), {
    FORBID_TAGS: ["img","style","form","input","button","iframe"],
    FORBID_ATTR: ["style"],
  }));
</script>

<div class="markdown">{@html html}</div>

<style>
  .markdown{white-space:normal;font-size:13px;line-height:1.75;overflow-wrap:anywhere}.markdown :global(p){margin:0 0 12px}.markdown :global(p:last-child){margin-bottom:0}.markdown :global(h1){font-size:22px}.markdown :global(h2){font-size:18px}.markdown :global(h3){font-size:15px}.markdown :global(h1),.markdown :global(h2),.markdown :global(h3){margin:18px 0 10px}.markdown :global(pre){background:#10141b;border:1px solid var(--line);padding:14px;border-radius:6px;overflow:auto;margin:14px 0;white-space:pre;font-size:11px}.markdown :global(code){background:#252c36;border-radius:3px;padding:2px 4px;font-size:.9em}.markdown :global(pre code){background:transparent;padding:0;font-size:inherit}.markdown :global(a){color:var(--accent)}.markdown :global(ul),.markdown :global(ol){padding-left:22px}.markdown :global(blockquote){margin-left:0;border-left:3px solid #61784e;padding-left:15px;color:var(--muted)}.markdown :global(table){border-collapse:collapse;display:block;max-width:100%;overflow:auto;margin:16px 0;font-size:12px}.markdown :global(td),.markdown :global(th){border:1px solid var(--line);padding:8px 11px;text-align:left}.markdown :global(th){background:#252c36}.markdown :global(hr){border:0;border-top:1px solid var(--line);margin:20px 0}
</style>
