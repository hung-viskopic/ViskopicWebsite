/** @type {import('next').NextConfig} */
const baseHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
];
const noDevices = { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" };
// The student viva page records answers, so only it may ask for the microphone and camera.
const vivaDevices = { key: "Permissions-Policy", value: "camera=(self), microphone=(self), geolocation=()" };

export default {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/((?!viva/).*)", headers: [...baseHeaders, noDevices] },
      { source: "/viva/:path*", headers: [...baseHeaders, vivaDevices, { key: "Cache-Control", value: "no-store" }] },
    ];
  },
};
