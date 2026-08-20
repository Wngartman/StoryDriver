import { motion } from "framer-motion";
import { AlertTriangle, X } from "lucide-react";

export default function ConfirmDialog({
  title,
  body,
  confirmLabel = "Delete",
  onCancel,
  onConfirm,
}) {
  return (
    <motion.div
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-[70] grid place-items-center bg-black/62 p-4 backdrop-blur-sm"
      exit={{ opacity: 0 }}
      initial={{ opacity: 0 }}
    >
      <motion.div
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="sd-modal w-full max-w-md rounded-xl border border-line bg-panel p-5 shadow-glow"
        exit={{ opacity: 0, scale: 0.98, y: 12 }}
        initial={{ opacity: 0, scale: 0.98, y: 12 }}
        transition={{ type: "spring", stiffness: 360, damping: 32 }}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="flex gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-red-400/25 bg-red-500/10 text-red-300">
              <AlertTriangle size={18} />
            </div>
            <div>
              <h2 className="text-base font-semibold text-zinc-100">{title}</h2>
              <p className="mt-1 text-sm leading-6 text-muted">{body}</p>
            </div>
          </div>
          <button
            aria-label="Cancel"
            className="sd-icon-button grid h-8 w-8 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-300"
            onClick={onCancel}
            type="button"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex justify-end gap-2">
          <motion.button
            className="sd-action-button sd-action-button-ghost rounded-lg border border-line px-4 py-2 text-sm font-medium text-zinc-200"
            onClick={onCancel}
            type="button"
            whileHover={{ y: -1 }}
            whileTap={{ scale: 0.97 }}
          >
            Cancel
          </motion.button>
          <motion.button
            className="rounded-lg bg-red-400 px-4 py-2 text-sm font-semibold text-red-950"
            onClick={onConfirm}
            type="button"
            whileHover={{ y: -1 }}
            whileTap={{ scale: 0.97 }}
          >
            {confirmLabel}
          </motion.button>
        </div>
      </motion.div>
    </motion.div>
  );
}
