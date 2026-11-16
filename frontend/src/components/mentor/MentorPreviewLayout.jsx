import { NavLink, Outlet } from "react-router-dom";
import "./mentor.css";

// Shell for the local preview of the live mentor pages (no sign-in, no sidebar).
// index.css locks html/body/#root (overflow: hidden) so each layout owns its
// scrolling; this shell has no inner scroll area, so it scrolls itself.
export default function MentorPreviewLayout() {
  return (
    <div className="h-dvh overflow-y-auto bg-canvas-alt text-main">
      <header className="mt-topbar">
        <span className="mt-brand">AI Mentor</span>
        <NavLink to="/mentor">Classroom</NavLink>
        <NavLink to="/mentor-studio">Mentor studio</NavLink>
        <NavLink to="/mentor-settings">Settings</NavLink>
      </header>
      <Outlet />
    </div>
  );
}
