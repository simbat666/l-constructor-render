import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === 'seatbelt';
const allowedDevHost = process.env.L_DEV_ALLOWED_HOST;

export default defineConfig(async () => {
  return {
    css: { postcss: { plugins: [tailwindcss()] } },
    server:
      isCodexSeatbeltSandbox || allowedDevHost
        ? {
            ...(isCodexSeatbeltSandbox
              ? { watch: { useFsEvents: false, usePolling: true } }
              : {}),
            ...(allowedDevHost ? { allowedHosts: [allowedDevHost] } : {}),
          }
        : undefined,
    plugins: [vinext()],
  };
});
