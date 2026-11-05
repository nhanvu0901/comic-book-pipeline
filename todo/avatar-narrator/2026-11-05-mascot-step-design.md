# Đề xuất: bước "Mascot" cuối pipeline — chọn vị trí, ghép face + body, chỉnh chuyển động — 2026-11-05

**Trạng thái:** đã code (`stages/mascot/`, `ui/screens/s9_mascot.py`, kit `assets/mascot/rook_v1/`). Mục 8 là bản chốt; mục 1–7 là đề xuất ban đầu, chỗ nào khác mục 8 thì theo mục 8.

## 1. Ý tưởng chính

- Thêm **bước 9 "Mascot"** vào UI, nằm sau bước 8 "Video" (`ui/screens/s5_video.py`).
- **Đầu vào là `final.mp4` đã render xong**, không đụng vào đó. **Đầu ra là `final_mascot.mp4`**: video được encode lại một lần, âm thanh copy nguyên.
- Vì chạy sau cùng nên **pipeline cũ không thay đổi gì**. Chỉnh rồi render lại bao nhiêu lần cũng được, mỗi lần mất khoảng 15 giây cho một video 35 giây (đã đo trên Mac).

## 2. Bộ nhân vật ghép được ("mascot kit")

Tách nhân vật thành các lớp **thay thế cho nhau**, vẽ trên cùng một khung mẫu, có **điểm neo cổ** chung để face nào cũng gắn khít vào body nào:

| Lớp | Ví dụ | Ghi chú |
|---|---|---|
| **body** (thân + tay/cánh) | đứng, chỉ tay, giơ hai tay, chống cằm, cầm sách, nhún vai, khoanh tay | Mỗi body là một ảnh nguyên khối |
| **face** (mắt + mỏ/miệng) | cười híp, giật mình, nghi ngờ, buồn ngủ, cười khẩy, tức giận, buồn, hét | Gắn lên body tại điểm neo |
| **phụ kiện** (tuỳ chọn) | kính một tròng, mũ, khăn đổi màu | Bật/tắt riêng |
| **viền sticker + bóng** | tự sinh bằng code | Không cần vẽ |

- **Ghép:** 7 body × 8 face = 56 tổ hợp, chỉ từ 15 tranh. Artist vẽ ít, mình có nhiều biểu cảm.
- **Preset:** đặt tên sẵn cho tổ hợp hay dùng, ví dụ `point` = body:chỉ_tay + face:cười_híp, `shock` = body:giơ_tay + face:giật_mình. Hệ thống tự gợi ý dùng preset; Master đổi face/body tuỳ ý.
- **Thư mục:** `assets/mascot/rook_v1/`, gồm `bodies/*.png`, `faces/*.png`, `accessories/*.png` và một `kit.json`. File này khai: kích thước khung mẫu, điểm neo cổ của từng body, chỗ đặt của từng face, danh sách preset.
- **Thêm body hoặc face mới:**
  - bỏ PNG vào thư mục rồi bấm "Quét lại";
  - hoặc bấm nút **"Thêm body/face"** trong UI để upload. UI kiểm kích thước khung, cho kéo chỉnh điểm neo, xem trước với mọi face/body, rồi lưu vào `kit.json`.
- **Version:** kit có version (`rook_v1`, `rook_v2`). Project cũ vẫn trỏ version cũ, render lại ra y hệt.

## 3. Màn hình "Mascot" (bước 9)

```
┌──────────────── 9:16 preview ────────────────┐ ┌──────── Cấu hình chung ─────────┐
│  khung video ở thời điểm đang chọn           │ │ Kit: rook_v1                     │
│  + mascot vẽ đè, KÉO THẢ để đổi vị trí       │ │ Vị trí: [Góc trái-dưới ▼]       │
│  + lớp mờ vùng UI Shorts (bật/tắt)           │ │   trái-dưới / phải-dưới / giữa  │
│  + đường gióng: "chân chạm mép dưới",        │ │   / tuỳ chỉnh (x, y)            │
│    "đứng trên mép panel"                     │ │ Kích thước: ──●── 23% khung     │
│                                              │ │ Viền sticker ☑  Bóng ☑          │
│  [◀ frame]  [▶ phát 3 s]  [frame ▶]          │ │ Độ mạnh chuyển động:            │
└──────────────────────────────────────────────┘ │   nhẹ / vừa / sôi động          │
                                                  │ Nhún theo giọng ☑               │
┌──────────── Timeline (cue) ──────────────────┐ │ Né mặt nhân vật trong tranh ☑   │
│ 0s     5s     10s     15s     20s     25s    │ │ [Lưu làm mặc định cho kênh]     │
│ [▲vào·chỉ tay]  [lặn]   [ló·suy nghĩ] …      │ └─────────────────────────────────┘
│                        [⚡giật mình] [→ra]    │ ┌──────── Cue đang chọn ───────────┐
│ [Gợi ý tự động]  [+ Thêm cue]                │ │ Thời điểm: 25.76 s (kéo được)   │
└──────────────────────────────────────────────┘ │ Body: [giơ hai tay ▼]           │
                                                  │ Face: [giật mình ▼]             │
[Xem thử đoạn này (540p)]  [Render final_mascot] │ Move: [rung ▼]  Vị trí: [chung ▼]│
[Tải về] [Mở trong tab mới]                       └─────────────────────────────────┘
```

- **Cấu hình chung:** vị trí, kích thước, viền, độ mạnh chuyển động.
  - **Vị trí chọn bằng preset hoặc kéo thả** trên khung xem trước. Có nút gióng sẵn **"chân chạm mép dưới"** như bản demo vừa làm.
  - **"Lưu làm mặc định cho kênh"**: mọi video mới mở ra là có sẵn vị trí, kích thước bạn đã chọn.
- **Timeline gồm các cue.** Mỗi cue là một thời điểm, một tổ hợp body + face, một move và một vị trí (theo cấu hình chung hoặc riêng cho cue đó).
  - Move có sẵn: trồi lên, ló từ cạnh, lặn xuống, trượt ra, nhảy, nghiêng, rung, đổi tư thế bằng cú pop. Mọi move đều chồng lên chuyển động trôi và thở luôn chạy nền.
  - Thao tác: bấm vào cue để sửa; kéo cue trên timeline để đổi thời điểm; thêm hoặc xoá cue.
- **"Gợi ý tự động"** dựng cue từ dữ liệu sẵn có:
  - HOOK thì vào và chỉ tay; CLIMAX/twist thì giật mình và rung; LANDING thì suy nghĩ hoặc cười khẩy; cuối video thì trượt ra.
  - Thời điểm lấy từ beat timing và điểm cắt cảnh trong `shots.json`.
  - Cue bạn đã sửa tay được **khoá**, bấm gợi ý lại không bị ghi đè.
- **"Né mặt nhân vật trong tranh":** dùng `char_boxes` của Magi để biết shot nào có mặt nhân vật ở góc mascot đang đứng. Nếu bị che, UI gợi ý đổi sang góc kia và tô đỏ cue đó; Master quyết.
- **"Xem thử đoạn này":** render nhanh 540×960 cho đúng 3–5 giây quanh cue đang chọn, vài giây là xong. **"Render final_mascot"** mới làm bản đầy đủ.
- **Cảnh báo:** khi mascot đè lên vùng caption hoặc vùng UI Shorts, UI chỉ cảnh báo, không chặn.

## 4. Dữ liệu lưu

`projects/<slug>/mascot.json` (per project); `assets/mascot/channel_defaults.json` (mặc định kênh):

```json
{
  "kit": "rook_v1",
  "enabled": true,
  "position": {"preset": "bottom_left", "x": 30, "y": 1502, "snap": "feet_on_bottom_edge"},
  "size_pct": 23,
  "outline": true, "shadow": true,
  "motion": "normal", "talk_bounce": true, "avoid_faces": true,
  "cues": [
    {"t": 0.20, "body": "point",   "face": "happy", "move": "rise",  "locked": false},
    {"t": 2.05, "body": "chin",    "face": "sly",   "move": "pop",   "locked": false},
    {"t": 5.55, "move": "sink"},
    {"t": 25.76, "body": "arms_up", "face": "shock", "move": "shake", "locked": true, "position": {"preset": "bottom_right"}},
    {"t": 34.70, "move": "slide_right"}
  ]
}
```

## 5. Render

- Giải mã `final.mp4`, vẽ mascot lên từng frame bằng Python (body + face + phụ kiện tại điểm neo, rồi biến đổi cả hình), encode H.264, copy âm thanh. Code: `stages/mascot/render.py`, cấu hình đọc từ `mascot.json`.
- **Nhún theo giọng:** đọc file giọng đọc riêng (`audio.wav`, không có nhạc) để mascot chỉ nhún khi narrator nói. Không có file đó thì dùng bản mix.
- **Chạy nền:** render chạy như job nền (giống Stage 5 trên server), báo tiến độ lên UI.

## 6. Lộ trình

| Giai đoạn | Việc | Ước lượng |
|---|---|---|
| P1 | Kit face+body (`kit.json`), lệnh `python -m stages.mascot --project <slug>` đọc `mascot.json`, gợi ý cue tự động | 2–3 ngày |
| P2 | Màn hình bước 9: cấu hình chung, danh sách cue sửa được, xem thử 540p, render và tải về | 3–4 ngày |
| P3 | Kéo thả trên khung, lớp vùng UI, gióng "chân chạm mép", né mặt nhân vật, upload body/face mới | 2–3 ngày |

## 7. Cần Master quyết

1. **Số nhân vật:** cả kênh dùng **một kit** (chỉ Rook), hay cho phép nhiều nhân vật?
2. **Thứ tự lớp:** mascot nằm **trên caption** (che được chữ) hay luôn né vùng caption?
3. **Danh sách đặt artist:** đề xuất 7 body + 8 face + 2 phụ kiện. Bạn muốn thêm hay bớt tư thế nào?
4. **File xuất:** `final_mascot.mp4` để riêng cạnh `final.mp4`, hay thay luôn `final.mp4` khi bật mascot?

---

## 8. Quyết định của Master (2026-11-05) và thiết kế chốt

| # | Câu hỏi | Quyết định | Hệ quả |
|---|---|---|---|
| 1 | Một hay nhiều nhân vật | **Mỗi video một nhân vật**, nhưng có danh sách để chọn | Bước 9 có ô "Nhân vật"; kit mặc định của kênh lưu trong `channel_defaults.json`, mỗi project có thể đổi |
| 2 | Mascot với caption | **Caption Master tự làm sau trong CapCut** | Không né caption tự động; Master tự đặt vị trí mascot. Có thể vẽ thêm một khung gợi ý "vùng caption CapCut" (bật/tắt) |
| 3 | Bộ đặt artist | **Đồng ý** 7 body + 8 face + 2 phụ kiện | Spec kit ở mục 2 |
| 4 | File xuất | **Ghi thẳng vào `final.mp4`** | Lần đầu bật mascot, bản sạch được chuyển thành `mascot/final_clean.mp4` (không để trong `_stage5/` vì nút "Delete _stage5 scratch" sẽ xoá nó). Mọi lần render mascot đều đọc từ bản sạch (không chồng 2 lớp). Tắt mascot thì khôi phục bản sạch về `final.mp4`. Render lại Stage 5 thì tạo bản sạch mới |

### 8.1 Danh sách nhân vật để chọn
Đã phác 9 nhân vật gốc cùng một style (nét mực đậm, màu phẳng, viền sticker); Master chọn **Rook**, các bản phác khác đã xoá. Kit hiện tại là bản vẽ bằng code (`assets/mascot/rook_v1/_source/draw_kit.py`), artist có thể vẽ lại thành `rook_v2` cùng cấu trúc.
1. Rook — quạ đeo kính một tròng
2. Gutter — bé đầu lâu áo choàng
3. Inky — giọt mực sống
4. Panel-head — đầu là khung panel
5. Hex — mèo đen đội mũ trùm
6. Boo — ma nhỏ ôm truyện
7. Hoot — cú thủ thư
8. Frame-bot — robot màn hình CRT
9. Imp — quỷ con tinh nghịch

### 8.2 Xem trước bằng ẢNH TĨNH, kéo thả
- **Ảnh xem trước:** lấy frame tại thời điểm đang chọn bằng `ffmpeg -ss t -frames:v 1` từ bản sạch, cache theo t. Mascot vẽ đè lên ảnh bằng đúng tổ hợp body + face của cue đó. Không phát video trong UI.
- **Kéo thả:**
  - **Kéo mascot** trên ảnh để đổi vị trí; **kéo góc** để đổi kích thước.
  - **Gióng tự động:** chân chạm mép dưới; đứng trên mép panel (lấy từ khung panel trong `shots.json`); góc trái/phải; giữa.
  - **Phạm vi áp dụng:** vị trí vừa kéo áp cho **cue đang chọn**, hoặc tick "áp cho mọi cue" để đổi vị trí chung.
- **Bóng mờ cue kề bên:** hiện mờ vị trí của cue trước và cue sau, để thấy quỹ đạo nhân vật sẽ đi.

### 8.3 Cơ chế đặt cue
- **Thanh timeline** dưới ảnh:
  - một dải thumbnail, mỗi shot một ảnh lấy từ `shots.json`;
  - dạng sóng giọng đọc;
  - vạch các điểm cắt cảnh, các mốc beat (HOOK / CLIMAX / LANDING) và từng từ (nếu có `word_timestamps.json`).
- **Đặt cue:**
  - **Bấm vào timeline** để đặt con trỏ thời gian, ảnh xem trước nhảy tới đó. Bấm **"+ Cue tại đây"**, hoặc nhấp đúp lên timeline.
  - **Kéo cue** trái/phải để đổi thời điểm. Cue **tự hít** vào điểm cắt cảnh, mốc beat hoặc đầu một từ gần nhất (giữ Alt để tắt hít).
  - **Mỗi cue** là một con chip trên timeline: icon move + mặt nhỏ của face. Bấm chọn để sửa ở panel bên phải: body, face (chọn từ lưới thumbnail), move (vào, ló, nhảy, nghiêng, rung, đổi tư thế, lặn, trượt ra), vị trí (chung hay riêng).
- **Khoảng giữa hai cue:** nhân vật giữ tư thế của cue trước, cộng chuyển động trôi/thở. Cue "lặn" hoặc "trượt ra" làm nhân vật biến mất cho tới cue "vào" tiếp theo.
- **Nút "Gợi ý tự động":** tạo cue theo luật chung (mục 3). Cue Master đã sửa thì khoá lại. **"Xoá gợi ý"** chỉ xoá cue chưa khoá.
- **Xem thử chuyển động:** nút **"Xem thử 3 giây quanh cue"** render một clip 540p rất ngắn, vài giây là xong. Đây là chỗ duy nhất có video, vì ảnh tĩnh không cho thấy được chuyển động.
- **Render:** nút **"Render"** ghi `final.mp4`, theo mục 8, quyết định 4.

### 8.4 Chốt khi bắt đầu code (2026-11-05)
- Master chọn **Rook**; 8 nhân vật kia đã xoá.
- Toạ độ trong `mascot.json` tính theo **tỉ lệ khung**: `x` = tâm nhân vật theo chiều ngang (0..1), `y` = **đường chân** (1.0 = chân chạm mép dưới), `size_pct` = chiều cao khung kit theo % chiều cao video. Preview cỡ nào cũng khớp bản render 1080×1920.
- Move: `rise`, `peek` (vào); `pop`, `hop`, `tilt`, `shake`, `hold` (khi đang hiện); `sink`, `slide_out` (ra). `peek` và `slide_out` tự chọn cạnh gần nhất.
- Hợp đồng code nằm ở `stages/mascot/config.py` và docstring của các module trong `stages/mascot/`.
