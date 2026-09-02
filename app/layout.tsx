import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import SessionBoot from "@/components/SessionBoot";

export const metadata: Metadata = {
  title: "Instagram AI Auto-Generator",
  description:
    "Scrape a profile's top posts, re-create them as fully AI-generated branded posts, and confirm each one before it is generated.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div className="topbar-inner">
            <Link href="/" className="brand">
              INSTAGRAM <span>| FACTS4GENIUS</span>
            </Link>
            <nav className="topnav">
              <Link href="/">New job</Link>
              <Link href="/history">History</Link>
            </nav>
          </div>
        </header>
        <main className="shell">
          <SessionBoot>{children}</SessionBoot>
        </main>
      </body>
    </html>
  );
}
