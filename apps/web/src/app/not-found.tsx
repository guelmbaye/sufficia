import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-[900px] px-6 py-16">
      <h1 className="font-expanded text-3xl font-extrabold">This page does not exist</h1>
      <p className="mt-3 text-ink-2">The link may point to a run from another server. Start a new run from the mission.</p>
      <Link href="/" className="mt-6 inline-block rounded-sm bg-ink px-5 py-2.5 font-semibold text-paper">
        Go to the mission
      </Link>
    </div>
  );
}
