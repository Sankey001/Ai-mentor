import { NavLink, Outlet } from "react-router-dom";
import "./mentor.css";

// Shell for the local preview of the live mentor pages (no sign-in, no sidebar).
export default function MentorPreviewLayout() {
  return (
    <div className="min-h-dvh bg-canvas-alt text-main">
      <header className="mt-topbar">
        <span className="mt-brand">AI Mentor</span>
        <NavLink to="/mentor">Classroom</NavLink>
        <NavLink to="/mentor-studio">Mentor studio</NavLink>
      </header>
      <Outlet />
    </div>
  );
}
