import React from 'react'
import { NavLink } from 'react-router-dom'
import { ShieldCheck, LayoutDashboard, FileKey, RefreshCw, Bot, Settings as SettingsIcon, LogOut } from 'lucide-react'

const links = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/certificates', label: 'Certificates', icon: FileKey },
  { to: '/renewals', label: 'Renewals', icon: RefreshCw },
  { to: '/agent', label: 'AI Agent', icon: Bot },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
]

export default function Navbar({ onLogout }) {
  return (
    <nav className="border-b border-border bg-panel">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="text-accent" size={22} />
          <span className="font-semibold text-sm tracking-wide">CERT LIFECYCLE PLATFORM</span>
        </div>
        <div className="flex items-center gap-1">
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm transition ${
                  isActive ? 'bg-accent/10 text-accent' : 'text-muted hover:text-slate-200'
                }`
              }
            >
              <Icon size={16} /> {label}
            </NavLink>
          ))}
          <button onClick={onLogout} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-muted hover:text-danger transition">
            <LogOut size={16} /> Logout
          </button>
        </div>
      </div>
    </nav>
  )
}
