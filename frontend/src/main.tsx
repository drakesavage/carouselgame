import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource/chakra-petch/500.css";
import "@fontsource/chakra-petch/700.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/400-italic.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/600.css";
import "@fontsource/literata/400.css";
import "@fontsource/literata/400-italic.css";
import "@fontsource/literata/600.css";
import "./styles.css";
import "./hud-v2.css";
import "./story-v3.css";
import App from "./App";
import "./theme-dcc.css"; // last: the Dungeon Crawler World broadcast theme overrides the base styles

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
