import PortalHome from "../shared/PortalHome.jsx";

// /superadmin — Superadmin portal: every module, incl. Users & Access, Audit Logs and Rates & Rules.
export default function Home() {
  return (
    <PortalHome
      eyebrow="SUPERADMIN PORTAL"
      title="System Administration"
      intro="You have access to every module, including user accounts, audit logs and system settings."
    />
  );
}
