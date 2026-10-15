export async function endpointFingerprint(endpoint: string): Promise<string> {
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(endpoint),
  );
  return Array.from(new Uint8Array(hash), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}

export function applicationKey(value: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(
    atob(value.replace(/-/g, "+").replace(/_/g, "/")),
    (c) => c.charCodeAt(0),
  );
}

export async function browserSubscription(): Promise<PushSubscription | null> {
  if (
    !window.isSecureContext ||
    !("serviceWorker" in navigator) ||
    !("PushManager" in window)
  )
    return null;
  return (
    (
      await navigator.serviceWorker.getRegistration()
    )?.pushManager.getSubscription() ?? null
  );
}

export async function logoutPushFingerprint(): Promise<string | null> {
  try {
    return await Promise.race([
      browserSubscription().then((sub) =>
        sub ? endpointFingerprint(sub.endpoint) : null,
      ),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 1500)),
    ]);
  } catch {
    return null;
  }
}

export async function unsubscribeBrowser(): Promise<void> {
  try {
    await (await browserSubscription())?.unsubscribe();
  } catch {
    /* The server also revokes the device on logout. */
  }
}
