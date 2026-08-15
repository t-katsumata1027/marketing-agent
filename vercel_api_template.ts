// app/api/approve/route.ts: Next.js App Router に配置する最小テンプレート。
import { createHash } from "crypto";
import { createClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

function client() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) throw new Error("Supabase 設定がありません");
  return createClient(url, key, { auth: { persistSession: false } });
}
function hash(value: string) { return createHash("sha256").update(value).digest("hex"); }

// GET は確認ページのみで、DB を変更しない。
export async function GET(request: Request) {
  const url = new URL(request.url);
  if (!url.searchParams.get("id") || !url.searchParams.get("token")) return NextResponse.json({ error: "不正な承認 URL です" }, { status: 400 });
  return new NextResponse(`<!doctype html><meta charset="utf-8"><title>投稿承認</title><form method="post"><input type="hidden" name="token" value="${url.searchParams.get("token")!.replaceAll('"', '&quot;')}"><button type="submit">この投稿を承認する</button></form>`, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "referrer-policy": "no-referrer", "x-robots-tag": "noindex" } });
}

// POST のみが RPC を通じてトークンを消費し、状態を変更する。
export async function POST(request: Request) {
  const form = await request.formData(); const token = form.get("token");
  if (typeof token !== "string" || token.length < 40) return NextResponse.json({ error: "不正なトークンです" }, { status: 400 });
  try {
    const ip = request.headers.get("x-forwarded-for")?.split(",")[0] ?? "";
    const { data, error } = await client().rpc("approve_post_with_token", { p_token_hash: hash(token), p_ip_hash: ip ? hash(ip) : null, p_user_agent_hash: hash(request.headers.get("user-agent") ?? "") });
    if (error) return NextResponse.json({ error: "承認できませんでした" }, { status: 409 });
    return NextResponse.json({ success: true, postId: data });
  } catch { return NextResponse.json({ error: "一時的に処理できません" }, { status: 500 }); }
}
