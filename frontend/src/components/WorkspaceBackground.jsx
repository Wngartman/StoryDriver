import { useEffect, useMemo, useState } from "react";
import { API_BASE_URL } from "../api.js";
import { useAppStore } from "../store/useAppStore.js";


const fileUrl = (item) => `${API_BASE_URL}${item.url}`;

export default function WorkspaceBackground() {
  const items = useAppStore((state) => state.backgroundLibrary.items || []);
  const settings = useAppStore((state) => state.uiSettings);
  const isGenerating = useAppStore((state) => state.isGenerating);
  const selectedItems = useMemo(() => {
    const selected = new Set(settings.background_selected_ids || []);
    return items.filter((item) => selected.has(item.id));
  }, [items, settings.background_selected_ids]);
  const [currentId, setCurrentId] = useState(null);
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState !== "hidden");

  useEffect(() => {
    if (!selectedItems.length) {
      setCurrentId(null);
      return;
    }
    if (!selectedItems.some((item) => item.id === currentId)) setCurrentId(selectedItems[0].id);
  }, [currentId, selectedItems]);

  useEffect(() => {
    const onVisibility = () => setPageVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const currentIndex = Math.max(0, selectedItems.findIndex((item) => item.id === currentId));
  const nextIndex = selectedItems.length > 1 ? (currentIndex + 1) % selectedItems.length : currentIndex;
  const current = selectedItems[currentIndex] || null;
  const next = selectedItems[nextIndex] || null;

  useEffect(() => {
    for (const item of [current, next]) {
      if (!item) continue;
      const image = new Image();
      image.decoding = "async";
      image.src = fileUrl(item);
    }
  }, [current, next]);

  useEffect(() => {
    const intervalSeconds = Number(settings.background_rotation_seconds) || 0;
    const pausedForWriting = settings.background_pause_while_writing && isGenerating;
    if (selectedItems.length < 2 || intervalSeconds < 30 || !pageVisible || pausedForWriting) return undefined;
    const timer = window.setInterval(() => {
      setCurrentId((activeId) => {
        const index = Math.max(0, selectedItems.findIndex((item) => item.id === activeId));
        if (settings.background_rotation_order === "random") {
          const candidates = selectedItems.filter((item) => item.id !== activeId);
          return candidates[Math.floor(Math.random() * candidates.length)]?.id || activeId;
        }
        return selectedItems[(index + 1) % selectedItems.length].id;
      });
    }, intervalSeconds * 1000);
    return () => window.clearInterval(timer);
  }, [isGenerating, pageVisible, selectedItems, settings.background_pause_while_writing, settings.background_rotation_order, settings.background_rotation_seconds]);

  if (!current) return null;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const fadeSeconds = settings.motion === "off" || reducedMotion ? 0 : settings.background_fade_seconds;
  return (
    <div aria-hidden="true" className="sd-workspace-background">
      <div
        className="sd-workspace-background-image"
        key={current.id}
        style={{
          backgroundAttachment: settings.background_attachment,
          backgroundImage: `url("${fileUrl(current)}")`,
          backgroundPosition: settings.background_position,
          backgroundSize: settings.background_fit === "fill" ? "100% 100%" : settings.background_fit,
          filter: `blur(${settings.background_blur}px) saturate(${settings.background_saturation})`,
          opacity: settings.background_opacity,
          animationDuration: `${fadeSeconds}s`,
          transitionDuration: `${fadeSeconds}s`,
        }}
      />
      <div className="sd-workspace-background-dim" style={{ opacity: settings.background_dim }} />
    </div>
  );
}
