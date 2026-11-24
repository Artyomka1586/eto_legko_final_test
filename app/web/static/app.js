// Поиск офферов при добавлении в поток.
// Обработчики на document — переживают замену DOM после htmx-свопов.

document.addEventListener("input", (event) => {
  const input = event.target.closest(".offer-search");
  if (!input) return;
  const form = input.closest("form");
  const list = form.querySelector(".offer-list");
  clearTimeout(input._timer);

  const query = input.value.trim();
  if (query.length < 2) {
    list.hidden = true;
    return;
  }
  input._timer = setTimeout(async () => {
    try {
      const response = await fetch("/api/offers?q=" + encodeURIComponent(query));
      const offers = await response.json();
      list.innerHTML = "";
      for (const offer of offers) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "offer-item";
        item.textContent = offer.name;
        item.addEventListener("click", () => {
          form.querySelector('[name="offer_id"]').value = offer.id;
          input.value = offer.name;
          list.hidden = true;
        });
        list.appendChild(item);
      }
      list.hidden = offers.length === 0;
    } catch {
      list.hidden = true;
    }
  }, 250);
});

// Закрытие выпадающего списка при клике мимо него.
document.addEventListener("click", (event) => {
  if (event.target.closest(".offer-list") || event.target.closest(".offer-search")) return;
  document.querySelectorAll(".offer-list").forEach((el) => { el.hidden = true; });
});
