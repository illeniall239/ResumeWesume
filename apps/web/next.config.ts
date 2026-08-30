import type { NextConfig } from 'next';

// The API origin. Same-origin by default via rewrites, so the browser never
// needs CORS in development and deployment is a single hostname.
const API_ORIGIN = process.env.API_ORIGIN || 'http://127.0.0.1:8000';

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: '/api/:path*', destination: `${API_ORIGIN}/api/:path*` },
      { source: '/docs', destination: `${API_ORIGIN}/docs` },
      { source: '/openapi.json', destination: `${API_ORIGIN}/openapi.json` },
    ];
  },
};

export default nextConfig;
