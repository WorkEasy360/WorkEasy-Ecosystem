import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Workeasy360",
  description: "One ecosystem to run your entire business.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>
        <a href="#w360-main" className="w360-skip-link">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
