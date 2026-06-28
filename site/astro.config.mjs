// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
  // Update to your real domain once deployed on the VPS.
  site: 'https://ccx.local',
  vite: {
    plugins: [tailwindcss()],
  },
});
