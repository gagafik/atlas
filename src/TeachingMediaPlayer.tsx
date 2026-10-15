import { useEffect, useRef, useState } from "react";
import "./TeachingMediaPlayer.css";

export function mediaKind(filename: string): "audio" | "video" | null {
  if (/\.(mp3|wav|m4a|ogg)$/i.test(filename)) return "audio";
  if (/\.(mp4|webm)$/i.test(filename)) return "video";
  return null;
}

export default function TeachingMediaPlayer({
  filename,
  url,
}: {
  filename: string;
  url: string;
}) {
  const kind = mediaKind(filename);
  const player = useRef<HTMLMediaElement | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
    const media = player.current;
    if (!media) return;
    // Restore after a URL change or React's development effect cleanup.
    if (media.getAttribute("src") !== url) media.setAttribute("src", url);
    const pauseHidden = () => {
      if (document.hidden) media.pause();
    };
    const pauseOther = (event: Event) => {
      if ((event as CustomEvent).detail !== media) media.pause();
    };
    const details = media.closest("details");
    const collapse = () => {
      if (details && !details.open) media.pause();
    };
    document.addEventListener("visibilitychange", pauseHidden);
    window.addEventListener("atlas-material-play", pauseOther);
    details?.addEventListener("toggle", collapse);
    return () => {
      document.removeEventListener("visibilitychange", pauseHidden);
      window.removeEventListener("atlas-material-play", pauseOther);
      details?.removeEventListener("toggle", collapse);
      media.pause();
      media.removeAttribute("src");
      media.load(); // Cancel further reads when the view/permission context is removed.
    };
  }, [url, kind]);
  if (!kind) return null;
  const props = {
    className: "teaching-media-player",
    src: url,
    controls: true,
    preload: "none" as const,
    "aria-label": `${kind === "audio" ? "Аудио" : "Видео"}: ${filename}`,
    onError: () => {
      if (player.current?.error) setFailed(true);
    },
    onPlay: () =>
      window.dispatchEvent(
        new CustomEvent("atlas-material-play", { detail: player.current }),
      ),
  };
  return (
    <div className="teaching-media">
      {kind === "video" ? (
        <video
          {...props}
          ref={(node) => {
            player.current = node;
          }}
          playsInline
        />
      ) : (
        <audio
          {...props}
          ref={(node) => {
            player.current = node;
          }}
        />
      )}
      {failed && (
        <p role="status">
          Не удалось воспроизвести файл. Обновите страницу, чтобы проверить
          доступ. Если доступ есть, скачайте файл и откройте в совместимом
          проигрывателе.
        </p>
      )}
    </div>
  );
}
