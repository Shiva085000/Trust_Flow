/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx,js,jsx}",
  ],
  theme: {
    // Override ALL border radius to 0 — sharp edges only
    borderRadius: {
      none: "0",
      sm: "0",
      DEFAULT: "0",
      md: "0",
      lg: "0",
      xl: "0",
      "2xl": "0",
      "3xl": "0",
      full: "9999px", // keep for pill badges only
    },
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1600px" },
    },
    extend: {
      colors: {
        // shadcn/ui variable-based colors (values overridden in index.css)
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Direct terminal palette utilities
        terminal: {
          bg:          "var(--bg-primary)",
          nav:         "var(--header-bg)",
          card:        "var(--bg-card)",
          input:       "var(--bg-primary)",
          border:      "var(--border)",
          "border-hi": "var(--accent-blue)",
          blue:        "var(--accent-blue)",
          "blue-dim":  "#1d4ed8",
          green:       "var(--accent-green)",
          amber:       "var(--accent-amber)",
          red:         "var(--accent-red)",
          violet:      "#a78bfa",
          text:        "var(--text-primary)",
          secondary:   "var(--text-secondary)",
          muted:       "var(--text-muted)",
          dim:         "var(--text-muted)",
          deepdim:     "var(--border)",
        },
      },
      fontFamily: {
        // All body/data text uses JetBrains Mono
        mono:   ["JetBrains Mono", "Courier New", "monospace"],
        sans:   ["system-ui", "sans-serif"],
        // Titles/headers use Space Grotesk
        header: ["Space Grotesk", "system-ui", "sans-serif"],
      },
      keyframes: {
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%":      { opacity: "0.15" },
        },
        "accordion-down": {
          from: { height: "0" },
          to:   { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to:   { height: "0" },
        },
      },
      animation: {
        "pulse-dot":      "pulse-dot 2s ease-in-out infinite",
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up":   "accordion-up 0.2s ease-out",
      },
      boxShadow: {
        "blue-glow":        "0 0 20px rgba(37,99,235,0.15), 0 0 40px rgba(37,99,235,0.06)",
        "blue-glow-strong": "0 0 20px rgba(37,99,235,0.45), 0 0 60px rgba(37,99,235,0.2)",
        "green-glow":       "0 0 12px rgba(22,163,74,0.3)",
        "card-hover":       "0 0 30px rgba(37,99,235,0.12), inset 0 0 0 1px rgba(37,99,235,0.2)",
      },
    },
  },
  plugins: [],
};
