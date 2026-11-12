// Keep keyboard focus inside a modal and restore it when the modal closes.
export function modalFocus(node, oncancel) {
  const previous = document.activeElement;
  const controls = () => [...node.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]')];
  queueMicrotask(() => (controls()[0] || node).focus());
  function keydown(event) {
    if (event.key === "Escape") { event.preventDefault(); oncancel?.(); }
    if (event.key !== "Tab") return;
    const items = controls();
    if (!items.length) { event.preventDefault(); node.focus(); return; }
    const index = items.indexOf(document.activeElement);
    if (event.shiftKey && index <= 0) { event.preventDefault(); items.at(-1).focus(); }
    else if (!event.shiftKey && (index === items.length - 1 || index < 0)) { event.preventDefault(); items[0].focus(); }
  }
  node.addEventListener("keydown", keydown);
  return { update(value) { oncancel = value; }, destroy() { node.removeEventListener("keydown", keydown); previous?.focus(); } };
}
