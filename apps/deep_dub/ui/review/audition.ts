/** 独立试听互斥；浮层使用 Portal，也必须纳入同一个监听范围。 */
export function stopAuditions(except?: HTMLMediaElement) {
  document
    .querySelectorAll<HTMLMediaElement>(
      ".dd-studio audio,.dd-floating audio,.dd-floating video",
    )
    .forEach((media) => {
      if (media !== except) media.pause();
    });
}
