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
//
// Issue #360: 「値が変わったことをどう検知してアニメーションを発火させるか」の
// 仕組みをオプトインで追加する。対象要素に`id`と`data-animate-on-change="count"`を
// 付けておくと、bodyの差し替え前後でその要素のテキストを比較し、値が変化していれば
// 数値カウントアップを再生する。見た目(発光など)は付与しない(生成/削除するのは
// `data-animate-on-change`属性値と同名のクラス`nss-animate-count`のみ)ため、
// 装飾自体は各ウィジェット固有のCSSに委ねられ、他ウィジェットが将来同種の
// アニメーションを必要とした際もこの仕組みをそのまま再利用できる。
// 新旧いずれかが数値としてパースできない場合(セッション開始直後の初回表示、
// 「-」→数値の遷移。#359参照)は無演出のまま最終値を表示する
// (`parseInt("-", 10)`はNaNになるため、初回だけ除外する特別な分岐は不要)。
(function () {
  var scriptTag = document.currentScript;
  var intervalMs = parseInt(scriptTag.getAttribute("data-interval-ms"), 10);
  if (!intervalMs || intervalMs <= 0) {
    intervalMs = 5000;
  }

  var COUNT_ANIMATION_DURATION_MS = 600;
  var COUNT_ANIMATION_CLASS = "nss-animate-count";
  var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function snapshotAnimatedValues() {
    var snapshot = {};
    document.querySelectorAll("[data-animate-on-change]").forEach(function (el) {
      if (el.id) {
        snapshot[el.id] = el.textContent;
      }
    });
    return snapshot;
  }

  function animateCount(el, fromValue, toValue) {
    var start = null;
    el.classList.add(COUNT_ANIMATION_CLASS);
    function step(timestamp) {
      if (start === null) {
        start = timestamp;
      }
      var progress = Math.min((timestamp - start) / COUNT_ANIMATION_DURATION_MS, 1);
      var eased = 1 - Math.pow(1 - progress, 3); // easeOutCubic
      el.textContent = Math.round(fromValue + (toValue - fromValue) * eased);
      if (progress < 1) {
        requestAnimationFrame(step);
      } else {
        el.textContent = toValue;
        el.classList.remove(COUNT_ANIMATION_CLASS);
      }
    }
    requestAnimationFrame(step);
  }

  function playAnimationsForChangedValues(previousValues) {
    document.querySelectorAll('[data-animate-on-change="count"]').forEach(function (el) {
      if (!el.id || !(el.id in previousValues)) {
        return;
      }
      var fromText = previousValues[el.id];
      var toText = el.textContent;
      if (fromText === toText || reducedMotion) {
        return;
      }
      var fromValue = parseInt(fromText, 10);
      var toValue = parseInt(toText, 10);
      if (isNaN(fromValue) || isNaN(toValue)) {
        return;
      }
      animateCount(el, fromValue, toValue);
    });
  }

  function refresh() {
    var previousValues = snapshotAnimatedValues();
    fetch(window.location.href, { cache: "no-store" })
      .then(function (response) {
        return response.text();
      })
      .then(function (html) {
        var newDocument = new DOMParser().parseFromString(html, "text/html");
        document.body.innerHTML = newDocument.body.innerHTML;
        playAnimationsForChangedValues(previousValues);
      })
      .catch(function () {
        // 通信エラー時は何もしない(次回のポーリングに任せる)
      });
  }

  setInterval(refresh, intervalMs);
})();
