import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Vinext's Cloudflare worker does not inherit arbitrary shell variables at
  // request time. Inline only the non-secret loopback connector address; NVR
  // credentials remain in macOS Keychain and never enter the web bundle.
  env: {
    GATE_PERSON_AUDIT_BASE_URL: process.env.GATE_PERSON_AUDIT_BASE_URL ?? "",
  },
};

export default nextConfig;
