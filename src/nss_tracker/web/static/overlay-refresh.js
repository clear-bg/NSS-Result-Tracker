// Issue #104: 全overlay系ページ共通の自動更新スクリプト。
//
// OBSのブラウザソースは一度ページを読み込むと明示的にリロードしない限り表示が
// 固定されるため、定期的にDBの最新値を反映する仕組みが必要。<meta
// http-equiv="refresh">によるページ全体の定期リロードも検討したが、配信中に
// 毎回一瞬白塗き/点滅するリスクがあるため不採用にした(ユーザーとの相談で決定)。
// 代わりに、このスクリプト自身のページURL(=呼び出し元と同じURL)を定期的に
// fetchし直し、<body>の中身だけを差し替える(ページ遷移を伴わないため
// ちらつきが起きない)。この方式は全ページで共通のロジックであり、表示内容
// ごとの差分(得点/アシストか、SVGグラフか等)を一切気にせず使えるため、
// ウィジェットごとに更新方式がバラつくのを避けたいという#104の目的にも合う。
//
// 更新間隔は呼び出し元のscriptタグのdata-interval-ms属性で指定する
// (例: <script src="/static/overlay-refresh.js" data-interval-ms="5000"></script>)。
// 属性が無い/数値化できない場合は5000msをデフォルトにする。
(function () {
  var scriptTag = document.currentScript;
  var intervalMs = parseInt(scriptTag.getAttribute("data-interval-ms"), 10);
  if (!intervalMs || intervalMs <= 0) {
    intervalMs = 5000;
  }

  function refresh() {
    fetch(window.location.href, { cache: "no-store" })
      .then(function (response) {
        return response.text();
      })
      .then(function (html) {
        var newDocument = new DOMParser().parseFromString(html, "text/html");
        document.body.innerHTML = newDocument.body.innerHTML;
      })
      .catch(function () {
        // 通信エラー時は何もしない(次回のポーリングに任せる)
      });
  }

  setInterval(refresh, intervalMs);
})();
