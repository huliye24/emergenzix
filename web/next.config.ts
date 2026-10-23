import type { NextConfig } from "next";

const backendUrl = process.env.EMERGENZIX_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/health",
        destination: backendUrl + "/health",
      },
      {
        source: "/api/v1/:path*",
        destination: backendUrl + "/api/v1/:path*",
      },
    ];
  },
};

export default nextConfig;
