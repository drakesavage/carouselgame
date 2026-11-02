import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource/eb-garamond/400.css";
import "@fontsource/eb-garamond/400-italic.css";
import "@fontsource/eb-garamond/600.css";
import "@fontsource/cabin/400.css";
import "@fontsource/cabin/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/600.css";
import "./styles.css";
import "./hud-v2.css";
import "./story-v3.css";
import App from "./App";
import "./theme-carousel.css"; // last: the Carousel tokens and looks override the base styles

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
