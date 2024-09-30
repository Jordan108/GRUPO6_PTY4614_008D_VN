import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

import Navbar from "@/components/Navbar";
import Aside from "@/components/Aside";
import AiConfig from "@/components/AiConfig";
import { RecordingProvider } from "@/context/RecordingContext";
import { MetricsProvider } from '@/context/MetricsContext'; // Importa el MetricsProvider
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "EnganchAI",
  description: "Aplicación de IA para la detección de enganches en clases",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body className={`${inter.className} flex flex-col min-h-screen`}>
        <RecordingProvider>
          <MetricsProvider>
            <Navbar />
            <main className="relative flex-1 flex overflow-hidden">
              <Aside />
              <AiConfig />
              <div className="flex-1 overflow-auto">
                {children}
              </div>
            </main>
            {/* Toast container */}
            <ToastContainer
              autoClose={2000}
              position='bottom-center'
              newestOnTop={false}
              rtl={false}
              closeOnClick
            />
          </MetricsProvider>
        </RecordingProvider>
      </body>
    </html>
  );
}
