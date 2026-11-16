/* LifeOps — theme-init.js
   Runs in <head> before the page paints, so a saved light/dark theme never
   flashes the wrong colours. (A separate file: the page allows no inline
   scripts.) The key matches THEME_KEY in app.js. */
(function () {
  'use strict';
  try {
    const theme = localStorage.getItem('lifeops-theme');
    if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme);
  } catch {
    // Storage blocked: the system theme is used.
  }
})();
