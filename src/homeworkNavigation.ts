export function canLeaveHomework() {
  return window.dispatchEvent(
    new Event("atlas-homework-leave", { cancelable: true }),
  );
}
