# 夕食Instagramの画像表示・差戻し再通知

本人の復旧依頼に基づき、10/30夕食Instagram一件だけを再発行する。本体・食事・AI・画像生成・SNS予約公開は変更しない。他七件の本人承認と旧画像/差戻し/要求/受領を保持する。

実Discord GETで夕食IGだけ画像のwidth/heightが0、添付ファイルなし。他七件の画像寸法は正常、本人承認済み。夕食IGはchanges_requestedであり、自動再通知経路がない。前回のURL本文・SHA確認ではDiscord自体の表示失敗を検出できていなかった。

同じ画像SHA・本文・digest・予定で夕食IGだけrevision+1、新nonceの通常三ボタンを発行する。旧row/notice/receipt/表示失敗の実GETを専用台帳へ保存し、新通知はJPEGのmultipart直接添付にする。新request UUID・wire・nonce・画像SHAを一度のPOST前に永続化。同ID GETで送付先・著者・本文・ボタンに加え、Discord添付CDNの実バイトSHA/JPEG寸法と表示寸法を検査する。結果不明は再POSTしない。

旧06:30準備期限は維持する。今回の新しい本人依頼に基づく一件の再通知権限だけ18:30までとし、通常notifierと通常unknown照会は専用行を処理できない。履歴登録・新版/nonce/wire固定・sending claimは同一SQLite transaction。POST直前にも期限・構成SHA・claim所有を確認する。

元receiptの除外は専用履歴との完全一致が成立した旧版だけ。新版受領と本人の再承認が揃うまで六件cohortの公開停止を維持する。時刻を過ぎた朝食は再公開せず、元の本人承認・版・予定・nonce失効が確認できるexpiredだけを決定済み終端として扱う。未承認expired・unknownは引き続き拒否する。

独立計画Go後に競合/通常照会迂回のP1指摘を反映。専用5件、morning11件、通知5件、既存IG15件を隔離で成功。固定版の最終独立Go・CI・配置・実添付再受領は結果として追記する。

## 送信前停止の補修

5bd3c801は独立Go・CI40966192034成功後に配置し、切替時130台帳全行不変。専用実行は履歴と新版rev2/通知78を原子的に保存した後、-mの__main__と通常importでContextVarが二重になり送信前guardで停止した。新通知のroutine receipt行は0でtransmit未到達、Discord追加POST0。旧unit/停止原本は保持する。

入口はcanonical imported runへ集約する。同じ登録済みUUID/通知78/版2/wire/nonceだけの送信前継続を追加し、receipt行0・registered・sending・同payload/digest/予定・原画像を要求する。旧configは変更せず、新root継続原本で新コードcommit・旧config SHA・同UUID・通知ID・新rev・18:30を束縛する。POST直前は二原本ともSHAを再検査。送信済み/不明を未送信へ戻す経路ではない。
