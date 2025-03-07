import pickle
import argparse

PICKLE_FILE_PATH = "proceed.pickle"


def remove_from_sets_in_pickle(number_to_remove):
    """
    "proceed.pickle" ファイルに保存された複数のintのsetから、指定された数を削除し、
    変更をpickleファイルに保存します。

    Args:
        number_to_remove (int): 削除する数。
    """

    try:
        with open(PICKLE_FILE_PATH, 'rb') as file:
            data = pickle.load(file)
    except FileNotFoundError:
        print(f"エラー: ファイル '{PICKLE_FILE_PATH}' が見つかりません。")
        return
    except Exception as e:
        print(f"エラー: pickleファイルの読み込みに失敗しました: {e}")
        return

    # 各setから指定された数を削除
    for s in data:
        if isinstance(s, set):  # set型であるか確認
            s.discard(number_to_remove)

    # 変更をpickleファイルに保存
    try:
        with open(PICKLE_FILE_PATH, 'wb') as file:
            pickle.dump(data, file)
    except Exception as e:
        print(f"エラー: pickleファイルへの書き込みに失敗しました: {e}")
        return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="proceed.pickleファイルから指定された数を削除します。")
    parser.add_argument("number_to_remove", type=int, help="削除する数")

    args = parser.parse_args()

    remove_from_sets_in_pickle(args.number_to_remove)

    print(f"{PICKLE_FILE_PATH} から {args.number_to_remove} を削除しました。")
