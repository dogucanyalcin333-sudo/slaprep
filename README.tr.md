*[English](README.md)*

# SLAPrep — SLA baskı öncesi otomatik hazırlık

[![tests](https://github.com/dogucanyalcin333-sudo/slaprep/actions/workflows/tests.yml/badge.svg)](https://github.com/dogucanyalcin333-sudo/slaprep/actions/workflows/tests.yml)

Bölünmüş figür kitlerini (STL) CHITUBOX'a hazır hale getirir: montajı çözer, ölçekler, hollow eder, drenaj
deliklerini **sadece birleşme yüzeylerine** açar, parçaları yönlendirir ve plate'lere dizer.
Birleşme yüzeyleri, parçalar birleştirilince komşu parçayla kapanır; baskıdan sonra delik tıkamak gerekmez.
CHITUBOX'ta size sadece destek ekleyip slice etmek kalır.

Kaidenin alt yüzü ve duvar modunda arka yüz de montajdan sonra görünmez, ama bunlar dış yüzeydir; delik açılırsa
açık kalır. Bu yüzden varsayılan olarak buralara delik açılmaz. İsterseniz *Hollow / delik* sekmesinden
açabilirsiniz (`--holes-on-bottom`, `--holes-on-wall-back`).

Windows için yazılmıştır, Python 3.11+ gerekir. Yazdığı plate'ler düz STL olduğu için her reçine dilimleyicisi
açar (CHITUBOX, Lychee, Photon Workshop, Halot Box); sadece süre tahmini sizin girdiğiniz katman ayarlarına dayanır.

## İki dakikada deneyin

Elinizde kit yok mu? Kodla üretilen (telif derdi olmayan) küçük bir bölünmüş figür oluşturup hazırlayın:

```
python tools\make_demo_kit.py demo-kit
slaprep prep demo-kit --height 90 --printer elegoo-mars-5
```

Deneme kitinde kaide, gövde, iki kol, iki alternatif kafa (`Head_v1` / `Head_v2`) ve bir ölçek referans çubuğu
var; pim/yuvalar gerçek. Montaj çözümü, alternatif seçimi, hollow, birleşme yüzeyine delik ve plate yerleşimi
tek çalıştırmada görülür.

## Kurulum

Depoyu indirin (GitHub'da **Code → Download ZIP**, ya da `git clone`), sonra `install.bat` dosyasına
çift tıklayın. Sanal ortam kurulur ve masaüstüne **SLAPrep** kısayolu eklenir.
Klasörü taşırsanız `install.bat`'ı tekrar çalıştırın.

```
git clone https://github.com/dogucanyalcin333-sudo/slaprep.git
cd slaprep
install.bat
```

## Kullanım (pencere)

Pencere dört bölümdür: üstte **kit seçimi** (klasör, Gözat, Rapor, Klasörler), solda **ayar kartları**, sağda
**sekmeler** (Parçalar, Plate'ler, Özet, Montaj kontrolü, Kuyruk), altta **işlem çubuğu** (Analiz et, Hazırla,
Durdur, Kuyruğa ekle, Düşük öncelik, Günlük). Ayarların yanındaki ⓘ işaretinin üzerine gelince açıklaması görünür.
SLAPrep tek pencerede çalışır: açıkken kısayola klasör bırakırsanız yeni pencere açılmaz, kit açık pencerede açılır.

1. Masaüstündeki **SLAPrep** kısayolunu açın. Kit klasörünü pencereye ya da kısayolun üstüne sürükleyip bırakın
   (ya da üstteki **Gözat…**).
2. **Analiz et**: montaj, yükseklik, alternatif parçalar (V1/V2, birleşik/bölünmüş) ve gizli yüzeyler bulunur.
3. Sol kenardaki kartlardan ayarları seçin (kart başlığına tıklayınca açılır/kapanır; açık kalanlar hatırlanır):
   - **İşlemler**: iki onay kutusu. **Hollow + delik** (içi boşaltılır, delikler sadece birleşme yüzeylerine) ve
     **Yönlendir + plate'e diz**. İkisi açık = tam hazırlık. Üçüncü kutu **Kit hazır desteklenmiş (pre-supported)**:
     destekleri dosyada gelen kitler için. Parçalara dokunulmaz — hollow edilmez, delik açılmaz, döndürülmez —
     yalnızca plate'e dizilir; çünkü hollow destek köklerini açar, yeniden yönlendirmek desteklerin yönünü bozar. Sadece plate işaretliyse parçalar dolu kalır; sadece
     hollow işaretliyse plate dizilmez, parçalar orijinal duruşlarında ayrı STL olarak çıkar. Kapalı işlemin ayar
     kartı "işlem kapalı" diye kapanır; ana düğmenin adı da değişir (Hazırla / Plate'e diz / Hollow + delik).
   - **Boyut**: Orijinal / Hedef boy / Ölçek. Hedef boyda "Kaide dahil" ya da "Sadece figür". Altında basılacak
     boy yazar. Montaj bulunamazsa ürün sayfasındaki orijinal boyu girin.
   - **Hollow ve delik**: duvar kalınlığı, delik çapı (Otomatik: kavite büyüdükçe 3 → en büyük çap; Sabit), kavite
     başına en az delik. *Ek delik yerleri* (kaide altı, duvar arkası, görünür yüzey) dış yüzeye delik açar ve
     baskıdan sonra tıkanmalıdır; varsayılan olarak kapalıdır.
   - **Montaj**: *Duvara asılacak* anahtarı (arka yüzler delik için kullanılır), yukarı ekseni, **ön yönü**
     (destekler birleşme yerlerine, sonra figürün sırtına ve altına yönlendirilir; Montaj kontrolündeki "Önden"
     görünüm figürün sırtını gösteriyorsa buradan düzeltin), alt klasörler
     (işaretsiz klasör hazırlanmaz), alternatif parçalar (analizden sonra listelenir; desen de yazılabilir, ör.
     `*_Key`), "seçilmeyen alternatifleri de bas", "ölçek referansı / reklam parçalarını da bas".
   - **Yazıcı ve plate**: **Yazıcı**nızı profil listesinden seçin — plate boyutu oradan gelir, sığmayan parça
     sığacağı ölçekle bildirilir. Listede yoksa **Özel**'i seçip `218x123x230` yazın ve Kaydet'e basın; kaydedilir
     ve listeye eklenir. Profil değerleri üretici verilerinden alınmıştır, uzun bir baskıdan önce dilimleyicinizdeki
     değerle karşılaştırın. Sonra hedef: **En kısa süre** (varsayılan) ya da **En az plate**. SLA'da bir plate'in süresi en
     uzun parçasıyla belirlenir; "en kısa süre" uzun parçaları aynı plate'e toplar, kısa parçaları onların boşluğuna
     yerleştirir. Parça arası boşluk, klasörlere ayırma (Oto/Evet/Hayır) ve süre tahmini için CHITUBOX katman
     ayarlarınız.
   - **Reçine**: 1 litre fiyatı ve para birimi (listede yaygın olanlar var, istediğiniz kısaltmayı da
     yazabilirsiniz), isterseniz "ayrı plate" reçinesinin (ör. şeffaf) ayrı fiyatı, şişe boyu, destek +
     kayıp payı (%15). Fiyatlar kitte değil bu bilgisayarda saklanır; değiştirince sonuçlar yeniden hazırlamadan
     güncellenir.
4. **Parçalar** sekmesi:
   - Satırın başındaki kutu: parçayı **dahil et / hariç tut**. **Ayrı plate** sütunundaki kutu: parça diğerleriyle
     karışmadan kendi plate'lerine dizilir (`plates\ayrı\plate_01.stl`; farklı reçine için). **Doluluk** hücresine
     tıklayınca *Otomatik / Dolu / Hollow* seçilir (Dolu: araç hollow edebilecek olsa da dolu basılır, ör. şeffaf
     reçine; Hollow: hacim eşiğinin altında kalsa da hollow edilir).
   - Birden çok parçayı (Ctrl/Shift) seçince üstteki **seçim çubuğundan** hepsinin doluluk, ayrı plate ve dahil
     ayarı birlikte değişir; karışık durum ara işaretle gösterilir. Sağ tık menüsü de aynı işleri yapar.
   - **Parça ara** kutusu listeyi süzer. Analizden/hazırlıktan sonra hacim, sonuç (Hollow/Dolu/Basılmaz), delikler,
     hazır reçine miktarı ve plate sütunları dolar. Tercihler hazırlıkta kite kaydedilir.
5. **Montaj kontrolü** sekmesi: montajın önden/yandan görüntüsü (**Önden / Yandan / Boy / Fotoğraf**) ve her
   parçanın nasıl yerleştiği. Yeşil = güvenilir, turuncu = kontrol edin, kırmızı = yerleşmedi. Sekme adındaki sayı
   kontrol edilecek parça sayısıdır. Bir parça yanlış yerdeyse listeden seçip:
   - **Montajdan ayır** (montaja ve boya katılmaz, yine basılır), ya da
   - doğru komşusunu (çapa) açılır listeden seçip **Bağla** (aynı komşuya bağlanan parçalar — iki el gibi —
     birlikte, çakışmadan yerleşir); yanlış yere oturduysa **Başka konum dene**,
   - sonra **Montajı yeniden çöz**. Düzeltmeler kite kaydedilir.
   - Kaide ve figür klasörleri ayrı koordinatlarda geldiyse figür kaidenin yanında durur: figürün ayak parçasını
     seçip çapa olarak kaide parçasını seçin, **Bağla** — figür bütün olarak kaidenin üstüne oturur.
   - **Fotoğraf**: montajın 8 açıdan gri görüntüsü stüdyo fotoğrafıyla yan yana; açı ve fotoğraf okları,
     **Fotoğraf ekle…** (ya da fotoğrafı pencereye bırakın; kopyası `_slaprep\photos` altına alınır). Kitle gelen
     render'lar kendiliğinden listelenir. **Boy**: gerçek orantılı görünüm ve Boyut ayarına göre canlı cetvel.
   - Ölçek referans parçaları (ör. `HEIGHT_PILLAR`) ve indirme sitelerinin **reklam/filigran STL'leri** geometriden
     tanınır: basılmaz, boya katılmaz, raporda listelenir (basmak için Montaj kartındaki kutu / `--print-reference`).
   - Parçalar arasındaki 0.4–1.2 mm montaj boşluğu temas sayılır. Su geçirmez olmayan parça otomatik onarılır
     (minik delikler ve "kanat" üçgenler kayıpsız kapatılır); onarım şekli bozacaksa yapılmaz, parça orijinal
     hâliyle **dolu** basılır ve raporda nedeni yazar.
6. **Hazırla** (işlemlere göre adı "Plate'e diz" ya da "Hollow + delik" olur). Bitince:
   - **Özet**: basılacak parça, yükseklik, plate · süre ve reçine kartları; altında montaj bilgisi ve uyarılar.
   - **Plate'ler**: plate sayısı, toplam süre, reçine ve hedef kartları; plate başına parçalar, en yüksek parça,
     süre, reçine (ml · maliyet). Bir plate'e çift tıklarsanız STL varsayılan programla (CHITUBOX) açılır.
     **Gerçek süre**: bir plate'i bastıktan sonra yazıcının gösterdiği süreyi girin (ör. `3:31`); süre tahmini buna
     göre ayarlanır (tek ölçümde katman süresi, yükseklikleri farklı birkaç ölçümde plate başına ek süre de). Ölçümler
     bu bilgisayarda saklanır, tüm kitlerde ve komut satırında geçerlidir.
   - **Parçalar** tablosundaki **Destek** sütunu: destek gelecek alanın ne kadarının birleşme yeri, sırt ya da alt
     tarafta kaldığı. Yönlendirme, desteklerin önce birleşme yerlerine, sonra sırt ve alt kısma gelmesini; ön ve
     yanların temiz kalmasını tercih eder (raporda her parça için "destek: gizli % · arka/alt % · ön/yan %").
   - Üstteki **Rapor** görselli raporu, **Klasörler** plate / parça dosyalarını ve çıktı klasörünü açar.
7. Alt çubuktaki **Düşük öncelik** anahtarı işlemi düşük işlemci önceliğiyle çalıştırır (bilgisayar kullanılabilir
   kalır). **Günlük** çalışma çıktısını açar (hata olursa kendiliğinden açılır).
8. **Gece kuyruğu** (Kuyruk sekmesi): her kiti açıp ayarlarını yaptıktan sonra alttaki **Kuyruğa ekle**; sonra
   Kuyruk sekmesinde **Hepsini hazırla**. Kitler o anki ayarlarıyla sırayla hazırlanır; sabah her kitin sonucu
   (plate, süre, reçine, sorunlar) tek listede, satıra çift tıklayınca kit açılır. Kuyruk uygulama kapansa da
   saklanır; yarım kalan kit bir sonraki başlatmada baştan hazırlanır. Bir kit tek başına hazırlanırken de
   **Hepsini hazırla**'ya basabilirsiniz: kuyruk o iş bitince başlar; biten iş kuyrukta aynı ayarlarla bekliyorsa
   ikinci kez hazırlanmaz.

## Hızlı mod

Kit klasörünü `slaprep.bat` dosyasının üstüne bırakırsanız kayıtlı (ya da varsayılan) ayarlarla hazırlanır.

## Çıktılar (`<kit>\_slaprep\`)

| Dosya | İçerik |
|---|---|
| `plates\plate_01.stl` … | CHITUBOX'a açılacak asıl dosyalar. Kitte birden çok alt klasör varsa `plates\<klasör>\plate_01.stl` |
| `parts\` | Parça başına STL (yönlendirilmiş, hollow, delikli) |
| `report.html` | Görselli rapor |
| `prep.json`, `analysis.json` | Makinece okunur sonuçlar |
| `slaprep.yaml` | Bu kit için son ayarlar (bir sonraki çalıştırmada varsayılan olur) |

Orijinal STL dosyalarınıza asla dokunulmaz.

## Komut satırı

```
slaprep analyze "<klasör>"
slaprep prep "<klasör>" --height 150 [--height-mode figure] [--wall 2] [--hole 3 | --hole-max 5] [--holes 4]
                       [--variant "*_Key"] [--exclude Base,lab] [--wall-mount] [--up-axis y] [--only Head,Hat]
                       [--printer elegoo-saturn-4 | --build-volume 218x123x230] [--presupported]
slaprep scan "<kök klasör>"      # tüm kitleri sadece bbox okuyarak sınıflandırır
slaprep list                     # son çalıştırılan kitler
slaprep printers                 # yazıcı profilleri: id, ad, plate boyutu
slaprep printers --add "Benim yazıcım" 218x123x230     # kendi yazıcınızı kaydedin

# her komut --lang tr|en alır (arayüz dili; varsayılan işletim sisteminizden gelir)

# montaj düzeltmeleri (analyze ve prep; kite kaydedilir)
slaprep analyze "<klasör>" --detach Arm_Right,Sword_1        # yanlış yere oturanları montaja alma
slaprep analyze "<klasör>" --join Head_v2:Miquella_Torso     # ÇAPA:PARÇA — parçayı çapaya bağla
slaprep analyze "<klasör>" --join Torso:Hand_L:2             # ...ve 2. aday konumunu kullan
slaprep analyze "<klasör>" --clear-fixes                     # kayıtlı düzeltmeleri sil
```

## Sorun giderme

- **"Bellek yetmedi" / Windows "disk belleği dosyası çok küçük"**: disk yeri değil, RAM + sanal bellek bitti.
  Büyük kitlerde (parça başına 100–250 MB STL) bir parçanın yüklenmesi 5–6 GB bellek ister; SLAPrep paralel
  yüklemeyi boş belleğe göre ayarlar, yine de yetmezse açık programları (CHITUBOX, tarayıcı) kapatıp tekrar deneyin.
  Windows'un sayfa dosyasının bulunduğu sürücüde (Sistem → Gelişmiş → Performans → Sanal bellek) boş yer olmalı.
- Yarıda kalan bir çalıştırma sorun yaratmaz: önbellek dosyaları yarım yazılmaz, bir sonraki analiz kaldığı yerden
  (yüklenmiş parçaları atlayarak) devam eder.
- **"Yazıcıya sığmayan parçalar"**: parça plate'e konmaz ama `parts\` altında ayrı dosya olarak durur. Uyarıda
  sığacağı ölçek yazar (`--scale` ya da `--height` önerisi); ya kiti o kadar küçültün ya da parçayı CHITUBOX'ta bölün.
- **Önizlemede figür yan yatık**: yukarı ekseni, montajın devrilmeden durabildiği yönden bulunur; yine de yanlışsa
  `--up-axis y` (ya da `z`, `-y` …) ile düzeltin.
- **İstemediğiniz bir parça seti** (ör. başka dilde isim harfli ikinci kaide): `--exclude` klasör adını da tanır,
  alt-alt klasör dahil (`--exclude OUISTICRAM`).
- **Kitte aynı figürün birden çok sürümü var** (bölünmüş / tek parça / büst; adlarda Split, Merge, BUST gibi):
  en çok parçalı sürüm hazırlanır; diğeri için `--variant "*BUST*"` gibi seçin.
- **"Alt klasörler ayrı koordinatlarda"**: kaide ve figür klasörleri ayrı export edilmiş. Her biri kendi içinde
  doğru kurulur ve önizlemede yan yana durur; figürü kaideye nasıl oturtacağınız dosyalardan bilinmez. Oturtmak
  için Montaj kontrolü'nde figürün ayak parçasını kaide parçasına **Bağla** (CLI: `--join KAİDE:AYAK`).
- **"Baskı süresi için daha alçak bir yön seçildi"**: uyarıda yükseklik ve en büyük katman kesiti (önce → sonra)
  yazar. Alternatif yön en iyi yönden fazla deliksiz vantuz bırakamaz ve katman kesitini en çok 1.5 katına
  çıkarabilir (büyük kesit = FEP'ten soyulma kuvveti).
- **"Orijinde duran parçalar yerine oturtuldu"**: dosyada yanlış yerde unutulmuş parçalar ek yüzeylerinden
  yerleştirildi; Montaj kontrolü sekmesinde turuncu görünürler, önizlemeden kontrol edin.

## Geliştirme: testler ve regresyon seti

```
.venv\Scripts\python -m pytest tests -m "not rango"     # hızlı birim testleri (~1 dk, kit gerekmez)
.venv\Scripts\python tools\regress.py --quick           # bilinen kitlerde montaj sonucu beklenenle aynı mı
.venv\Scripts\python tools\regress.py                   # hepsi (Radahn, chiro, Rango otomatik dahil; uzun)
```

Regresyon seti kendi bilgisayarınızdaki kit klasörlerini gösterir, bu yüzden depoya girmez: biçimi
`tools\regress_kits.example.yaml` dosyasında: onu `tools\regress_kits.yaml` adıyla kopyalayıp kendi kitlerinizi
yazın. Rango testi için kit yolları `SLAPREP_RANGO` / `SLAPREP_RANGO_ORIG` ortam değişkenlerinden okunur
(verilmezse test atlanır).

Montaj, varyant ya da otomatik montaj kodunda her değişiklikten sonra regresyonu çalıştırın: daha önce doğru
çözülen bir kit bozulursa burada görünür. Yeni bir kit doğrulandığında (önizleme + gerçek boy) beklenen değerleriyle
`tools\regress_kits.yaml`'a ekleyin; `verified` alanına nasıl doğrulandığını yazın.

## Lisans

MIT — `LICENSE` dosyasına bakın.
