const API_KEY = "fd7d6a316db520c635dae95ce6df14ca";
const TOKEN = "ATTA901ad25eff569988f12ae4138ad081ef882162defa836223ad785aa824a942344F3A6EDD";
const BOARD_ID = "6a355a98aab74bc8f4acd983";

async function testBoard() {

  // Infos board
  let res = await fetch(`https://api.trello.com/1/boards/${BOARD_ID}?key=${API_KEY}&token=${TOKEN}`);
  let board = await res.json();
  console.log("Board:", board.name);

  // Lists
  res = await fetch(`https://api.trello.com/1/boards/${BOARD_ID}/lists?key=${API_KEY}&token=${TOKEN}`);
  let lists = await res.json();
  console.log("\nLists:");
  lists.forEach(l => console.log("-", l.name));

  // Cards
  res = await fetch(`https://api.trello.com/1/boards/${BOARD_ID}/cards?key=${API_KEY}&token=${TOKEN}`);
  let cards = await res.json();
  console.log("\nCards:");
  cards.forEach(c => console.log("-", c.name));
}

testBoard();
