import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import WikiPage from "./pages/WikiPage";
import ChatPage from "./pages/ChatPage";

export default function App() {
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
          <img src="/logo.png" alt="Wiki Agent" className="h-12 w-auto" />
          <nav className="flex gap-1 text-sm">
            <Tab to="/wiki" label="上传 Wiki" />
            <Tab to="/chat" label="提问知识" />
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 overflow-hidden px-6 py-4">
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/wiki" element={<WikiPage />} />
          <Route path="/chat" element={<ChatPage />} />
        </Routes>
      </main>
    </div>
  );
}

function Tab({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `rounded-md px-3 py-1.5 transition ${
          isActive
            ? "bg-slate-900 text-white"
            : "text-slate-600 hover:bg-slate-100"
        }`
      }
    >
      {label}
    </NavLink>
  );
}
