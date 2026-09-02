/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // The Python pipeline lives in a SEPARATE Vercel Service (../backend), not
  // inside this Next.js app. The root vercel.json's top-level rewrites send
  // /api/scrape, /api/scrape_poll, /api/analyze and /api/generate straight to
  // that service -- this app (and its own app/api/* Node routes, namespaced
  // under /api/jobs, /api/posts, /api/gate) never sees those four paths.
  // Nothing needs to be rewritten here for that reason; see ../vercel.json.
  eslint: {
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
