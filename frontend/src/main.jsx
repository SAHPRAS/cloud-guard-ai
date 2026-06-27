import React from "react";
import { createRoot } from "react-dom/client";
import RadarConsole from "./RadarConsole.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <RadarConsole />
  </React.StrictMode>
);
