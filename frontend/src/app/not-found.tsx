import Link from "next/link";

export default function NotFound() {
  return (
    <div
      style={{
        minHeight: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 16,
        padding: 24,
        textAlign: "center",
      }}
    >
      <h1 style={{ fontSize: 22 }}>Page not found</h1>
      <Link className="w360-btn w360-btn-primary" href="/">
        Back to Workeasy360
      </Link>
    </div>
  );
}
