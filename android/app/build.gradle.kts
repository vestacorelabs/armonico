import java.util.Base64

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// The project's version number is shared by every platform: the VERSION file at the top of the repository.
val repoRoot = rootProject.projectDir.parentFile
val shared = repoRoot.resolve("VERSION").readText().trim()

// Files that live once in the repository and are copied in at build time, never kept twice:
// the songs from setup/music and the Hebrew layout helper from lessons/.
val generatedAssets = layout.buildDirectory.dir("generated/armonico/assets")
val generatedPython = layout.buildDirectory.dir("generated/armonico/python")

val copyShared by tasks.registering {
    val music = repoRoot.resolve("setup/music")
    val lang = repoRoot.resolve("lessons/piano_lang.py")
    val ofl = repoRoot.resolve("lessons/fonts/OFL.txt")
    val lessons = repoRoot.resolve("lessons")
    val status = repoRoot.resolve("screen/piano-status.html")
    val version = repoRoot.resolve("VERSION")
    val tgSetup = repoRoot.resolve("setup/telegram_setup.py")
    val botPhoto = repoRoot.resolve("setup/bot-photo.jpg")
    inputs.dir(music)
    inputs.file(lang)
    inputs.file(ofl)
    inputs.dir(lessons)
    inputs.file(status)
    inputs.file(tgSetup)
    inputs.file(botPhoto)
    outputs.dir(generatedAssets)
    outputs.dir(generatedPython)
    doLast {
        val songs = generatedAssets.get().asFile.resolve("songs")
        songs.deleteRecursively()
        songs.mkdirs()
        music.listFiles { f -> f.name.endsWith(".mid") }?.forEach { it.copyTo(songs.resolve(it.name), true) }
        // the lesson engine, unchanged, in the folder layout it reads its files from
        // (lessons/, screen/piano-status.html, setup/music/, VERSION, the bot's profile); the app unpacks it on the phone
        val engine = generatedAssets.get().asFile.resolve("engine")
        engine.deleteRecursively()
        lessons.walkTopDown().filter { it.isFile && "__pycache__" !in it.path && !it.name.endsWith(".pyc") }.forEach {
            it.copyTo(engine.resolve("lessons/" + it.relativeTo(lessons).path), true)
        }
        status.copyTo(engine.resolve("screen/piano-status.html"), true)
        music.listFiles { f -> f.name.endsWith(".mid") }?.forEach { it.copyTo(engine.resolve("setup/music/" + it.name), true) }
        version.copyTo(engine.resolve("VERSION"), true)
        // the bot's description and picture, the same the Linux setup gives it (setup/telegram_setup.py)
        tgSetup.copyTo(engine.resolve("setup/telegram_setup.py"), true)
        botPhoto.copyTo(engine.resolve("setup/bot-photo.jpg"), true)
        // the font's license travels with the font (SIL OFL 1.1)
        ofl.copyTo(generatedAssets.get().asFile.resolve("licenses/OFL-Heebo.txt"), true)
        val py = generatedPython.get().asFile
        py.mkdirs()
        lang.copyTo(py.resolve("piano_lang.py"), true)
    }
}

android {
    namespace = "org.vestacorelabs.armonico"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.vestacorelabs.armonico"
        minSdk = 24
        targetSdk = 35
        // every build on GitHub gets a higher number, so a new APK installs over the old one
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        // For now the app is a step ahead of the project (shortcuts that act on the phone): 1.0.1 while the
        // project is 1.0. When the project reaches 1.1 this goes back to the shared number.
        versionName = "1.0.1"
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    // A release is signed with the project's own key when the four secrets are set (see
    // android/README.md). The same key on every build is what lets an update install over the
    // app without losing its settings. Without them the build is signed with a debug key.
    val keystoreB64 = System.getenv("ANDROID_KEYSTORE_B64")
    signingConfigs {
        if (!keystoreB64.isNullOrBlank()) {
            create("release") {
                val f = layout.buildDirectory.file("armonico.keystore").get().asFile
                f.parentFile.mkdirs()
                f.writeBytes(Base64.getMimeDecoder().decode(keystoreB64))
                storeFile = f
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (!keystoreB64.isNullOrBlank()) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }

    sourceSets["main"].assets.srcDir(generatedAssets)

    // screenshots of the screens on the computer, with no phone (./gradlew testReleaseUnitTest)
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                it.systemProperty("roborazzi.test.record", "true")
                it.systemProperty("robolectric.dependency.repo.url", "https://maven-central.storage-download.googleapis.com/maven2")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            // the same versions as requirements.txt
            install("paho-mqtt==2.1.0")
            install("mido==1.3.3")
            install("packaging==26.3")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
            srcDir(generatedPython)
        }
    }
}

tasks.named("preBuild") { dependsOn(copyShared) }
// the tasks that read the copied files say so, so Gradle runs the copy before them
tasks.matching { it.name.endsWith("PythonSources") || it.name.endsWith("Assets") }
    .configureEach { dependsOn(copyShared) }

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("com.google.android.gms:play-services-wearable:19.0.0")    // the watch app (WearSync.kt)
    implementation("androidx.fragment:fragment-ktx:1.8.5")   // Play services brings an old one, which lint refuses
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.17")
    testImplementation("io.github.takahirom.roborazzi:roborazzi:1.40.1")
    testImplementation("androidx.test:core:1.6.1")
    testImplementation("androidx.test.ext:junit:1.2.1")
}
