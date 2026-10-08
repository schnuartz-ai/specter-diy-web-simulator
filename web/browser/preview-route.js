/** Recognize both legacy and commit-specific isolated PR preview paths. */
export function isIsolatedPreviewRoute(pathname) {
  return /(?:^|\/)pr\/[1-9]\d*\/(?:[a-f0-9]{40}\/)?$/.test(pathname);
}
