import type { Metadata } from 'next'
import { GeistSans } from 'geist/font/sans'
import { GeistMono } from 'geist/font/mono'
import { NextIntlClientProvider } from 'next-intl'
import { getLocale, getMessages } from 'next-intl/server'
import { ThemeProvider } from 'next-themes'
import { DefaultThemeGuard } from '@/components/ui/default-theme-guard'
import { Toaster } from '@/components/ui/sonner'
import './globals.css'

export const metadata: Metadata = {
  title: 'CubePlex',
  description:
    'CubePlex is a self-hosted agent workspace for enterprise teams to delegate document, data, and cross-system automation while keeping permissions and execution boundaries under team control.',
  icons: {
    icon: [{ url: '/icon.png', type: 'image/png', sizes: '64x64' }],
  },
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const locale = await getLocale()
  const messages = await getMessages()
  return (
    <html
      lang={locale}
      suppressHydrationWarning
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <body className="font-sans">
        <NextIntlClientProvider locale={locale} messages={messages}>
          <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            // Operator family stays registered so its CSS classes still resolve.
            themes={['light', 'dark', 'operator-light', 'operator-dark']}
            // Mark the inline script as a data block so React 19 skips its
            // dev-only "script tag in component" warning; SSR execution is unchanged.
            scriptProps={{ type: 'text/javascript' }}
          >
            <DefaultThemeGuard />
            {children}
            <Toaster />
          </ThemeProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  )
}
