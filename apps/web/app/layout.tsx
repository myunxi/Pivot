import './globals.css';
import type { Metadata, Viewport } from 'next';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import Script from 'next/script';
import { SWRConfig } from 'swr';
import { ThemeProvider } from '@/components/theme-provider';
import { PluginThemeStyle } from '@/components/plugins/plugin-theme-style';
import { PostHogIdentify } from '@/components/posthog-identify';
import { DesktopAuthGate } from '@/components/dashboard/desktop-auth-gate';
import { DesktopAuthHandoff } from '@/components/dashboard/desktop-auth-handoff';
import { DesktopWindowControls } from '@/components/desktop/window-chrome';
import { DirectPairingGate } from '@/components/direct/direct-pairing-gate';
import { DirectPwa } from '@/components/direct/direct-pwa';
import {
  DIRECT_API_BASE_KEY,
  DIRECT_TOKEN_KEY,
  DIRECT_WS_URL_KEY,
} from '@/lib/direct-pairing';

// Desktop builds run their own local Next server with this flag set. As a
// compile-time constant it is identical on server and client, so it can gate
// desktop-only chrome below without a hydration mismatch.
const IS_DESKTOP = process.env.NEXT_PUBLIC_VICOA_DESKTOP === '1';

// Browser direct mode: the page itself injects the same runtime config the
// Electron preload normally provides. These are compile-time values, so a
// change requires a rebuild.
const IS_DIRECT_WEB = process.env.NEXT_PUBLIC_VICOA_DIRECT === '1';
const DIRECT_API_BASE = process.env.NEXT_PUBLIC_DIRECT_API_BASE ?? '';
const DIRECT_WS_URL = process.env.NEXT_PUBLIC_DIRECT_WS_URL ?? '';

const DIRECT_APPLE_WEB_APP = {
  capable: true,
  title: 'Vicoa',
  // Matches the dark app; with viewport-fit auto the page no longer paints
  // under the status bar, so an opaque bar keeps the top edge consistent.
  statusBarStyle: 'black',
} as const;

export const metadata: Metadata = {
  metadataBase: new URL('https://vicoa.ai'),
  title: 'Vicoa - Run & Manage a Team of AI Agents Anywhere',
  description: 'Vicoa is an agent-native IDE that lets you orchestrate parallel AI agents like Claude Code, Codex, Gemini, Cursor, and 40+ more on your phone and desktop. Put your coding agents in your pocket and start building from anywhere.',
  icons: [
    { rel: 'icon', url: '/favicon.ico', sizes: 'any' },
    { rel: 'shortcut icon', url: '/favicon.ico' },
    { rel: 'icon', url: '/favicon-192x192.png', sizes: '192x192', type: 'image/png' },
    { rel: 'icon', url: '/favicon-512x512.png', sizes: '512x512', type: 'image/png' },
    { rel: 'apple-touch-icon', url: '/favicon-apple.png', sizes: '180x180' },
  ],
  manifest: '/manifest.json',
  keywords: [
    'Vicoa',
    'Vicoa app',
    'Vicoa mobile',
    'agentic IDE',
    'ADE',
    'agentic development environment',
    'agentic development',
    'Remote control',
    'Claude Code remote control',
    'Codex remote control',
    'Opencode remote control',
    'Gemini remote control',
    'Cursor remote control',
    'Copilot remote control',
    'ACP agents',
    'Agent Client Protocol',
    'Code with AI',
    'coding agents',
    'mobile coding',
    'AI coding agents', 
    'Claude Code mobile',
    'Claude Code anywhere',
    'vibe coding',
    'vibe coding anywhere',
    'mobile development',
    'AI programming',
    'code anywhere',
    'code everywhere',
    'mobile AI assistant',
    'developer tools mobile',
    'developer tools everywhere',
    'coding on mobile',
    'coding everywhere',
    'AI-assisted development',
    'remote coding',
    'remote coding everywhere',
    'mobile code editor',
    'artificial intelligence coding',
    'programming assistant'
  ],
  authors: [{ name: 'Vicoa Team' }],
  creator: 'Vicoa',
  publisher: 'Vicoa',
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-video-preview': -1,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: 'https://vicoa.ai',
    siteName: 'Vicoa - Run & Manage a Team of AI Agents Anywhere',
    title: 'Vicoa - Run & Manage a Team of AI Agents Anywhere',
    description: 'Vicoa is an agent-native IDE that lets you orchestrate parallel AI agents like Claude Code, Codex, Gemini, Cursor, and 40+ more on your phone and desktop. Put your coding agents in your pocket and start building from anywhere.',
    images: [
      {
        url: '/images/vicoa-banner-v2.png',
        width: 1200,
        height: 630,
        alt: 'Vicoa - Run & Manage a Team of AI Agents Anywhere',
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    site: '@Vicoa',
    creator: '@Vicoa',
    title: 'Vicoa - Run & Manage a Team of AI Agents Anywhere',
    description: 'Vicoa is an agent-native IDE that lets you orchestrate parallel AI agents like Claude Code, Codex, Gemini, Cursor, and 40+ more on your phone and desktop. Put your coding agents in your pocket and start building from anywhere.',
    images: ['/images/vicoa-banner-v2.png'],
  },
  alternates: {
    canonical: 'https://vicoa.ai',
  },
  category: 'technology',
  classification: 'Developer Tools',
  applicationName: 'Vicoa',
  ...(IS_DIRECT_WEB ? { appleWebApp: DIRECT_APPLE_WEB_APP } : {}),
  referrer: 'origin-when-cross-origin',
  formatDetection: {
    email: false,
    address: false,
    telephone: false,
  },
  other: {
    'apple-itunes-app': 'app-id=6751626168',
    ...(IS_DIRECT_WEB ? { 'apple-mobile-web-app-capable': 'yes' } : {}),
  },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  // `cover` makes iOS standalone shrink the layout viewport by the status bar
  // height without moving its origin, leaving an unpaintable strip at the
  // bottom. Let iOS lay the app out inside the safe area instead.
  viewportFit: IS_DIRECT_WEB ? 'auto' : 'cover',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#1a1a1a' }
  ]
};

function jsString(value: string): string {
  return JSON.stringify(value).replace(/</g, '\\u003c');
}

function directRuntimeConfigScript(): string {
  return `(function(){
var TOKEN_KEY=${jsString(DIRECT_TOKEN_KEY)};
var API_KEY=${jsString(DIRECT_API_BASE_KEY)};
var WS_KEY=${jsString(DIRECT_WS_URL_KEY)};
var token='',apiBase='',wsUrl='',pairToken='';
try{
  var hash=window.location.hash||'';
  var match=hash.match(/[#&]token=([^&#]+)/);
  if(match){
    pairToken=decodeURIComponent(match[1]);
    token=pairToken;
    try{localStorage.setItem(TOKEN_KEY,token);}catch(e){}
    try{history.replaceState(null,'',window.location.pathname+window.location.search);}catch(e){}
  }else{
    try{token=localStorage.getItem(TOKEN_KEY)||'';}catch(e){}
  }
  try{apiBase=localStorage.getItem(API_KEY)||'';}catch(e){}
  try{wsUrl=localStorage.getItem(WS_KEY)||'';}catch(e){}
}catch(e){}
var proto=window.location.protocol==='https:'?'wss:':'ws:';
window.__VICOA_DESKTOP__={mode:'local',client:'web',direct:true,apiBase:${jsString(DIRECT_API_BASE)}||apiBase||window.location.origin,wsUrl:${jsString(DIRECT_WS_URL)}||wsUrl||proto+'//'+window.location.host+'/ws',token:token};
var path=window.location.pathname;
var onDashboard=path==='/dashboard'||path.indexOf('/dashboard/')===0;
if(pairToken){window.location.replace('/dashboard');}
else if(!token&&(path==='/'||onDashboard)){window.location.replace('/direct-pair');}
else if(token&&path==='/'){window.location.replace('/dashboard');}
})();`;
}

export default function RootLayout({
  children
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable} dark`}
      suppressHydrationWarning
    >
      <head>
        {/* Pre-hydration theme class. `<html>` ships with `dark` as the no-JS
            default; this blocking script runs before first paint and swaps in
            the user's stored choice so light-mode users never see a dark flash.
            Mirrors theme-provider.tsx: storageKey `ui-theme`, `system` resolves
            via matchMedia, and `plugin:` themes fall back to their dark base
            until the provider resolves the real base post-mount. Must stay a
            raw <script> (not next/script, which defers). */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('ui-theme')||'dark';var c='dark';if(t==='light')c='light';else if(t==='system')c=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';else if(t.indexOf('plugin:')===0)c='dark';var e=document.documentElement;e.classList.remove('light','dark');e.classList.add(c);}catch(e){}})();`,
          }}
        />
        {IS_DIRECT_WEB && (
          <script
            dangerouslySetInnerHTML={{ __html: directRuntimeConfigScript() }}
          />
        )}
        {!IS_DIRECT_WEB && (
          <>
            <Script
              src="https://www.googletagmanager.com/gtag/js?id=G-EWZH9BN2RK"
              strategy="lazyOnload"
            />
            <Script id="gtag-init" strategy="lazyOnload">
              {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('js', new Date());
            gtag('config', 'G-EWZH9BN2RK');
          `}
            </Script>
          </>
        )}
      </head>
      <body 
        className="min-h-[100dvh] bg-background text-foreground"
        suppressHydrationWarning
      >
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "Organization",
              "name": "Vicoa",
              "alternateName": "Vibe Code Anywhere",
              "url": "https://vicoa.ai",
              "logo": "https://vicoa.ai/images/vicoa-light.png",
            })
          }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "SoftwareApplication",
              "name": "Vicoa",
              "alternateName": "Vibe Code Anywhere",
              "url": "https://vicoa.ai",
              "description":
                "Vicoa lets you orchestrate dozens of coding agents in parallel, anywhere. Run Claude Code, Codex, and 40+ AI coding agents on any device — start on your laptop, switch seamlessly to mobile or web, get alerts when an agent needs input, and approve changes with one tap.",
              "applicationCategory": "DeveloperApplication",
              "operatingSystem": "Web, iOS, Android",
              "image": "https://vicoa.ai/images/vicoa-banner-v2.png",
              "screenshot": "https://vicoa.ai/images/hero.png",
              "publisher": {
                "@type": "Organization",
                "name": "Vicoa",
                "url": "https://vicoa.ai",
              },
              "offers": {
                "@type": "AggregateOffer",
                "priceCurrency": "USD",
                "lowPrice": "0",
                "highPrice": "12",
                "offerCount": "3",
              },
            })
          }}
        />
        {IS_DIRECT_WEB ? <DirectPairingGate /> : null}
        {IS_DIRECT_WEB ? <DirectPwa /> : null}
        {!IS_DIRECT_WEB ? <PostHogIdentify /> : null}
        <ThemeProvider defaultTheme="dark">
          {/* Injects token overrides for the active plugin theme (Tier 1); renders
              nothing for the built-in dark/light/system themes. */}
          <PluginThemeStyle />
          <SWRConfig value={{}}>
            {/* Desktop: mint the CLI key whenever a Supabase session appears in
                local mode. Mounted here (not just in the dashboard layout) so it
                also fires on the auth screens under login-required gating. */}
            {IS_DESKTOP ? <DesktopAuthHandoff /> : null}
            <DesktopAuthGate>{children}</DesktopAuthGate>
            {/* Custom title bar controls (min/max/close) for frameless windows —
                Windows, and Linux unless the user kept their desktop environment's
                title bar. Mounted last in DOM order so Electron's drag hit-test
                leaves them clickable over the surrounding drag strips; renders
                nothing on macOS/web. */}
            {IS_DESKTOP ? <DesktopWindowControls /> : null}
          </SWRConfig>
        </ThemeProvider>
      </body>
    </html>
  );
}
