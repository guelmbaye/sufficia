import type { NextConfig } from "next";

// The browser only ever talks to this origin. /api/v1/* is proxied to the SUFFICIA API by the route
// handler in src/app/api/v1/[...path]/route.ts, which reads SUFFICIA_API_URL at runtime.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The container build sets NEXT_OUTPUT=standalone (see Dockerfile); `next start` is used otherwise.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
