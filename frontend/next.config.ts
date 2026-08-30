import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // pin the tracing root to this package so `.next/standalone/server.js` lands at the top level
  // (a stray lockfile higher up the tree would otherwise make Next nest the output)
  outputFileTracingRoot: path.join(__dirname),
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: {
    optimizePackageImports: ["lucide-react", "recharts", "@react-three/drei"],
  },
};

export default nextConfig;
