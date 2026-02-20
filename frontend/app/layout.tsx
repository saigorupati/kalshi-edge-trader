import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Kalshi Edge Trader',
  description: 'Live trading dashboard for the Kalshi temperature edge strategy',
  icons: {
    icon: '/icon.svg',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-bg-primary text-text-primary min-h-screen antialiased">
        {children}
      </body>
    </html>
  );
}
