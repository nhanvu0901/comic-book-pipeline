# Kế hoạch làm mascot 2D: tạo nhân vật, giữ nhất quán, animation dựng sẵn — 2026-11-05

**Trạng thái:** đề xuất, chưa code. Đọc kèm `2026-11-05-avatar-narrator-research.md`.

## 0. GrandLineReview thực sự làm gì (tách frame 2 Short, 4 fps)

Hai video: `uN0IGW_XIKQ` (155k view) và `7etJA2fi8MY` (52k view). Cả hai đều 360×640, 24 fps, ~87 s.

- **Bố cục:**
  - Phần lớn thời lượng là **host thật quay mặt** (face-cam).
  - Phần còn lại là một "thẻ" tranh ngang 16:9 đặt giữa khung, trên và dưới là nền mờ của chính tranh đó.
  - Caption 2–3 từ đặt ở giữa-dưới, từ khoá tô xanh lá.
- **Mascot:** con cá sọc (nhân vật SBS của Oda), vẽ kiểu **sticker cắt viền** (viền vàng/trắng), đặt đè lên mép thẻ tranh.
  - **Không nhép miệng.** Nó có vài tư thế vẽ sẵn (đang chỉ tay; giật mình mắt trợn, miệng há) và đổi tư thế đúng chữ trong lời kể.
  - **Mượt vì luôn chuyển động nhẹ:** trôi, xoay, phóng vài phần trăm qua từng frame, không bao giờ đứng yên.
  - **Đổi tư thế bằng một cú "pop"** (phóng to rồi về) nên mắt người thấy như chuyển động, không thấy chỗ cắt.
- **Sticker nhân vật truyện** (Burgess, Doc Q…) cũng là ảnh cắt viền, bay vào và lắc nhẹ, kiểu sticker Telegram.

→ "Mượt" ở đây **không phải hoạt hình vẽ từng frame**, mà là **cut-out + biến đổi có easing + chuyển động phụ liên tục**. Kiểu này làm tự động được hoàn toàn, rẻ, chạy bằng CPU.

## 1. Tạo nhân vật (một avatar duy nhất)

1. **Thiết kế một lần** thành *model sheet*:
   - mặt trước và 3/4;
   - bảng màu (≤6 màu), độ dày nét, tỉ lệ đầu/thân;
   - 1–2 đặc điểm nhận dạng (ví dụ quạ "Rook" đeo kính một tròng và quàng khăn đỏ).
2. **Ai vẽ:**
   - **Khuyến nghị: thuê một artist vẽ vector có layer** ($100–300 cho trọn bộ ở mục 2). Hợp đồng ghi rõ quyền thương mại toàn phần và quyền chỉnh sửa.
   - Lý do không dùng AI vẽ bản cuối: nét AI dễ bị nhận ra (đúng cái "AI feel" cần bỏ), AI khó giữ đúng một nhân vật qua nhiều tư thế, và ảnh do AI tạo hoàn toàn không được bảo hộ bản quyền, nên mascot thương hiệu không thật sự là của mình.
   - **AI chỉ dùng ở bước phác ý:** sinh 10–20 phương án từ prompt, Master chọn một, artist vẽ lại sạch.
3. **Định dạng giao:** file vector (SVG/AI) hoặc PSD/Krita **tách layer**, xuất PNG alpha 2× (nhân vật cao ~1000 px để thu nhỏ vẫn nét).

## 2. Bộ phận cần vẽ ("rig pack" v1)

| Nhóm | Phần | Số lượng |
|---|---|---|
| Thân | body (1–2 dáng: đứng, nghiêng tới) | 2 |
| Đầu | head | 1 |
| Mắt | open, half, closed, wide (giật mình), happy (^ ^), side-eye | 6 |
| Mày | neutral, raised, angry, worried | 4 |
| Miệng/mỏ | closed, open-small, open-wide, smirk, shout + (tuỳ chọn) bộ A–H/X của Rhubarb | 5 (+9) |
| Tay/cánh | rest, point, shrug, facepalm, thumbs-up | 5 |
| Phụ kiện | khăn/đuôi/lông, những phần được lắc theo quán tính | 1–3 |
| Viền sticker | outline trắng/vàng dày quanh silhouette, theo phong cách GrandLineReview | 1 |

**Mỗi biểu cảm là một tổ hợp bộ phận, không phải một ảnh mới.** Ví dụ `shocked` = eyes:wide + brows:raised + mouth:open-wide + arms:rest. Vì mọi tư thế đều dựng từ cùng các bộ phận nên nhân vật luôn giống hệt nhau.

## 3. Giữ nhất quán

- **Không bao giờ sinh lại nhân vật cho từng video.** Mọi video render từ cùng một rig pack: `stages/avatar/assets/rook_v1/`.
  - Gồm các file PNG bộ phận và `rig.json`: pivot của từng phần, thứ tự layer, kích thước, vị trí neo, danh sách biểu cảm.
- Rig pack có **version** (`rook_v1`, `rook_v2`). Đổi thiết kế thì tạo version mới; project cũ vẫn trỏ version cũ, render lại ra giống hệt.
- **Render tất định:** cùng project và seed thì ra đúng cùng một chuyển động (blink, độ trôi). Test so hash frame.
- **Chọn biểu cảm theo luật chung,** lấy từ dữ liệu có sẵn (`function` của beat, cảm xúc của chunk TTS, từ khoá trong caption). Không viết riêng cho truyện nào.

## 4. Animation dựng sẵn

Mỗi "move" là một file dữ liệu khai báo keyframe theo thời gian: vị trí, xoay, phóng, độ mờ, và đổi bộ phận. Có đường easing riêng cho từng keyframe. Ví dụ `moves/shock.json`:

```json
{
  "name": "shock",
  "duration": 0.55,
  "swap": [{"t": 0.06, "eyes": "wide", "brows": "raised", "mouth": "open_wide"}],
  "tracks": {
    "scale_x":  [[0, 1.0], [0.06, 1.18, "out_quad"], [0.18, 0.92, "in_out_sine"], [0.32, 1.04, "in_out_sine"], [0.55, 1.0, "out_sine"]],
    "scale_y":  [[0, 1.0], [0.06, 0.84, "out_quad"], [0.18, 1.10, "in_out_sine"], [0.32, 0.98, "in_out_sine"], [0.55, 1.0, "out_sine"]],
    "shake_x":  {"amp_px": 7, "freq_hz": 18, "decay": 6.0}
  },
  "hold_pose_after": true
}
```

**Thư viện move cho MVP** (mỗi move tự trả về tư thế nghỉ, nên ghép tuỳ ý được):

| Move | Thời lượng | Cách chuyển động | Dùng khi |
|---|---|---|---|
| `enter_pop` | 0,35 s | Từ dưới lên, scale 0,6 → 1,08 → 1,0 (`out_back`), xoay −8° → 0° | Nhân vật xuất hiện |
| `idle` | lặp 3,2 s | Thở (scaleY 1,00 ↔ 1,02), nhún 3–4 px, lắc đầu ±1,5°, sine | Luôn chạy nền |
| `blink` | 0,12 s | open → half → closed → open, cách nhau ngẫu nhiên 2–5 s | Luôn chạy nền |
| `talk` | theo audio | Đầu nhún và miệng/mỏ mở theo biên độ giọng (RMS, làm mượt 60 ms) | Mỗi khi đang nói |
| `lean_in` | 0,4 s | Scale 1 → 1,12, dịch về phía tranh, `in_out_cubic`, giữ | HOOK |
| `point` | 0,3 s | Đổi tay sang point ở đỉnh một cú pop nhỏ, xoay hướng tranh | "Nhìn này", nhắc nhân vật |
| `shock` | 0,55 s | Squash → stretch → ổn định + rung tắt dần + mắt trợn | Twist / CLIMAX |
| `smirk` | 0,4 s | Nghiêng đầu 6°, mắt liếc, miệng smirk | Câu mỉa, hài |
| `laugh` | 0,8 s | Nảy 3 lần giảm dần, mắt ^ ^ | Chi tiết buồn cười |
| `shrug` | 0,6 s | Tay shrug, nhún vai, đầu nghiêng | LANDING mơ hồ |
| `exit_slide` | 0,3 s | Trượt ra cạnh (`in_back`) | Rời khung |

**Mẹo cho mượt** (cũng là cách GrandLineReview làm):
1. **Đổi bộ phận ở đỉnh cú pop/squash:** frame đổi ảnh trùng lúc nhân vật đang méo nhanh, nên mắt không thấy chỗ cắt.
2. **Chuyển động phụ có quán tính:** khăn, đuôi, lông bám theo đầu bằng lò xo tắt dần (spring/damper). Mỗi khi thân dừng, chúng còn đung đưa thêm một nhịp. Rẻ mà trông như vẽ tay.
3. **Không bao giờ đứng yên:** idle và blink luôn chạy dưới mọi move.
4. **Easing:** không dùng nội suy tuyến tính (`out_back` cho xuất hiện, `in_out_sine` cho thở, `out_quad` cho pop).
5. **Biến đổi ở độ chính xác dưới pixel:** dựng bộ phận ở 2× rồi thu về khi xuất. Dùng skia-python hoặc cairo cho viền khử răng cưa đẹp; Pillow `BICUBIC` cũng được ở bản đầu. Render đúng 30 fps của video.
6. **Giới hạn nhịp phản ứng:** tối thiểu 1,5–2 s giữa hai move lớn, tối thiểu 0,6 s cho mỗi tư thế.

## 5. Biên đạo tự động theo kịch bản

Đầu vào có sẵn: beat (`function` HOOK/ESCALATION/CLIMAX/LANDING), mốc thời gian beat (`stages/stage_4/beat_timing.py`), audio narration cuối, caption từng từ.

1. Mỗi beat chọn một move theo luật chung:

   | Beat | Move |
   |---|---|
   | HOOK | `enter_pop` + `lean_in` |
   | CLIMAX / twist | `shock` |
   | Câu có từ mỉa hoặc "!" | `smirk` |
   | LANDING | `shrug` hoặc `smirk` |
   | Beat thường | chỉ `talk` |

2. Move được đặt **đúng thời điểm của từ khoá** trong caption từng từ, ví dụ chữ "but", "suddenly", hoặc tên nhân vật bị lật mặt, để phản ứng khớp lời như GrandLineReview.
3. `talk` chạy bất cứ khi nào có giọng, chồng lên move đang giữ.
4. Master xem và **sửa được bằng tay** trong review UI: đổi move của một beat, hoặc tắt nhân vật ở beat đó. Lưu vào `review/avatar.json`, cùng mẫu với khoá panel.

## 6. Ghép vào video

- Render nhân vật thành luồng RGBA 30 fps, pipe thẳng vào ffmpeg; prototype ~1 s cho 5 s video.
- Một lượt `overlay` trên video đã ghép, gộp chung với bước burn caption để chỉ encode một lần.
- **Vị trí:** góc dưới-trái, cao ~18–22% khung, nằm **trên** vùng UI của Shorts (đáy 20–35% bị che). Đè nhẹ lên mép tranh như sticker.
- **Thời gian xuất hiện:** chỉ ở hook, twist, kết và vài nhịp ngắn, không phủ suốt.
- Cờ theo project, mặc định tắt; tắt thì output giữ nguyên từng byte.

## 7. Hai cách dựng move

| Cách | Mô tả | Ưu | Nhược |
|---|---|---|---|
| **A. Move khai báo bằng JSON, render bằng Python** (khuyến nghị) | Như mục 4: mình tự viết bộ render bộ phận + easing + lò xo | Toàn quyền, tất định, chạy trên server CPU, không phụ thuộc tool ngoài | Phải tự chỉnh keyframe; cần một màn xem thử |
| B. Animator làm move bằng tool rồi xuất **Lottie JSON** | After Effects + Bodymovin, hoặc Glaxnimate (miễn phí); pipeline render Lottie ra frame | Animator chỉnh được bằng mắt; chuẩn công nghiệp | Lottie không nhép miệng theo audio, nên miệng vẫn phải ghép riêng bằng Python; phải kiểm license thư viện render (rlottie/python-lottie) |

Bắt đầu bằng **A**. Nếu sau này thuê animator thì thêm B cho các move phức tạp.

## 8. Lộ trình

1. **Ngày 1–2:** chốt concept + model sheet (AI phác ý → Master chọn); đặt artist vẽ rig pack. Trong lúc chờ, dùng bản placeholder bằng hình khối.
2. **Ngày 3–5:**
   - bộ render bộ phận (2×, easing, lò xo);
   - 5 move: `enter_pop`, `idle`, `blink`, `talk`, `shock`;
   - một **màn xem thử move** (render một clip 10 s để Master duyệt độ mượt).
3. **Ngày 6–7:** biên đạo theo beat + từ khoá; overlay trong Stage 5 sau cờ.
4. **Ngày 8–10:**
   - thêm `lean_in`, `point`, `smirk`, `laugh`, `shrug`, `exit_slide`;
   - sửa tay trong review UI;
   - render thử trên server;
   - bắt đầu A/B theo cặp chủ đề.
