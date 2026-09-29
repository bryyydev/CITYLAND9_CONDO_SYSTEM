import PortalHome from "../shared/PortalHome.jsx";

// /staff — Staff portal: operational data entry only (move in/out, gate pass, expenses, attendance, maintenance).
export default function Home() {
  return (
    <PortalHome
      eyebrow="STAFF PORTAL"
      title="Daily Operations"
      intro="Record move-ins and move-outs, gate passes, expenses, attendance and maintenance tickets."
    />
  );
}
