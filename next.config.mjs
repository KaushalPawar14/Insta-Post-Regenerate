/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // The Python pipeline functions live in /api/*.py and are built by Vercel's
  // Python runtime, NOT by Next.js. Next.js's own route handlers live under
  // app/api/* and are Node functions. The two coexist because Vercel treats
  // root-level /api/*.py as file-based Python functions.
  //
  // Next.js must not try to claim those paths, so nothing is rewritten here --
  // /api/scrape, /api/scrape_poll, /api/analyze and /api/generate are served
  // by the Python runtime, and every Next.js route is namespaced under a
  // distinct path (/api/jobs, /api/posts, /api/gate).
  eslint: {
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
