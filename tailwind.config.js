/** Dashboard design tokens. Site is vanilla CSS; values live in static/styles.css :root. */
module.exports = {
  theme: {
    spacing: {
      0: "0px",
      1: "8px",
      2: "16px",
      3: "24px",
      4: "32px",
      5: "40px",
      6: "48px",
      7: "56px",
      8: "64px",
    },
    fontFamily: {
      sans: ["Manrope", "sans-serif"],
      mono: [
        "ui-monospace",
        "SFMono-Regular",
        "Menlo",
        "Monaco",
        "Consolas",
        "Liberation Mono",
        "Courier New",
        "monospace",
      ],
    },
    fontWeight: {
      medium: "500",
    },
    fontSize: {
      caption: ["12px", { lineHeight: "16px" }],
      heading: ["16px", { lineHeight: "24px", fontWeight: "500" }],
      metric: ["32px", { lineHeight: "40px" }],
    },
  },
};
