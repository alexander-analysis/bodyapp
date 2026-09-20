import { useState } from "react";
import { dismissBanner, shouldShowBanner, useInstall } from "../lib/install";

export function InstallBanner() {
  const install = useInstall();
  const [hidden, setHidden] = useState(!shouldShowBanner());
  if (hidden || install.standalone) return null;
  if (!install.canPrompt && !install.ios) return null;

  const close = () => {
    dismissBanner();
    setHidden(true);
  };
  return (
    <div className="card mb-4 flex items-start gap-3 text-sm">
      <div className="flex-1">
        <div className="font-medium">Install to your home screen</div>
        {install.ios ? (
          <p className="text-muted mt-1">
            In Safari: tap <span className="text-ink">Share</span> → <span className="text-ink">Add to Home Screen</span>. Needed for full-screen use and camera access.
          </p>
        ) : (
          <p className="text-muted mt-1">Full-screen, offline logging and camera access.</p>
        )}
      </div>
      <div className="flex flex-col gap-2">
        {install.canPrompt && (
          <button className="btn btn-primary py-2 text-sm" onClick={() => void install.prompt().then((r) => r !== "unavailable" && close())}>
            Install
          </button>
        )}
        <button className="btn btn-ghost py-2 text-sm" onClick={close}>
          Later
        </button>
      </div>
    </div>
  );
}
