/** The browser only ever talks to this server: /backend/* is proxied to the FastAPI gateway (no CORS, the API address stays server-side). */
const API_URL = process.env.API_URL || "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/backend/:path*", destination: `${API_URL}/:path*` },
      // the presenter's demo hub is served by the demo API (harness/demo.py); a real gateway has no /demo, so this is a 404 there
      { source: "/demo", destination: `${API_URL}/demo` },
      { source: "/demo/:path*", destination: `${API_URL}/demo/:path*` },
    ];
  },
};

export default nextConfig;
